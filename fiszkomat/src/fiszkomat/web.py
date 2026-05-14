"""fiszkomat Phase 1 web app.

FastAPI shell around fiszkomat.core, with Stripe Checkout gating.

Flow:
  POST /jobs (PDF)                  -> page-count, price
                                       if FREE token present:    queue + run
                                       else:                     create Stripe Checkout,
                                                                 return {checkout_url}
  GET  /pay/return?session_id=...   -> verify paid, queue + run
  POST /stripe/webhook              -> backup queue + run on
                                       checkout.session.completed
  GET  /jobs/{id}                   -> status JSON
  GET  /jobs/{id}/deck              -> .apkg download (only when status=done)

Run:
    python -m fiszkomat.web
"""
from __future__ import annotations

import asyncio
import html as html_lib
import json
import os
import re
import sys
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Literal

import pypdf
import stripe
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse

from .core import EmptyPdfError, RunStats, run as core_run
from .study_html import STUDY_HTML


# ---------- config ----------


MAX_PDF_BYTES = 30 * 1024 * 1024
MAX_PAGES = 500

# Pricing tiers (grosze; 100 grosze = 1 PLN). Tiered to protect margin on
# long scanned PDFs — OCR mode costs ~3× text mode per page; tier 3 (151–300p)
# and tier 4 (301–500p) absorb the worst-case OCR cost while keeping margin.
PRICE_TIERS = [
    (50,  300),   # ≤50p    → 3 PLN
    (150, 500),   # 51–150  → 5 PLN
    (300, 1000),  # 151–300 → 10 PLN
    (500, 1500),  # 301–500 → 15 PLN
]

# Quality-pass pricing — separate tier table (not a flat multiplier) so worst-
# case Opus 4.7 review cost is fully covered across all page ranges. Sized
# against ~$0.020/card Opus output + Haiku floor + vision-mode upside.
PRICE_TIERS_QP = [
    (50,  500),   # ≤50p    → 5 PLN  (cost ~3 PLN worst case → +2 zł / 40% margin)
    (150, 800),   # 51–150  → 8 PLN  (cost ~6 PLN → +2 zł / 25%)
    (300, 1600),  # 151–300 → 16 PLN (cost ~12 PLN → +4 zł / 25%)
    (500, 2500),  # 301–500 → 25 PLN (cost ~18 PLN → +7 zł / 28%)
]
JOB_TTL_SECONDS = 60 * 60
WORK_DIR = Path(os.environ.get("FISZKOMAT_WORK_DIR", Path.cwd() / "_work"))
WORK_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_BASE_URL = os.environ.get("FISZKOMAT_PUBLIC_BASE_URL", "http://127.0.0.1:8000")

# Load fiszkomat/.env only. Cross-project loading (invoice_idp/.env) was
# removed 2026-05-13 — each project owns its own least-privileged env.
load_dotenv(Path(".env"), override=True)

stripe.api_key = os.environ.get("STRIPE_SECRET", "")
STRIPE_PK = os.environ.get("STRIPE_PUBLISHABLE_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")


def price_for_pages(pages: int, quality_pass: bool = False) -> int:
    """Return price in grosze for a given page count, choosing the
    standard-tier or quality-pass-tier table."""
    table = PRICE_TIERS_QP if quality_pass else PRICE_TIERS
    for max_pages, price in table:
        if pages <= max_pages:
            return price
    return table[-1][1]


# ---------- job state ----------


JobStatus = Literal["pending_payment", "queued", "running", "done", "failed", "cancelled"]


@dataclass
class Job:
    job_id: str
    pdf_name: str
    pdf_pages: int
    status: JobStatus = "pending_payment"
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    log_lines: list[str] = field(default_factory=list)
    stats: RunStats | None = None
    error: str | None = None
    deck_path: Path | None = None
    pdf_path: Path | None = None
    cards_path: Path | None = None
    paid: bool = False
    stripe_session_id: str | None = None
    price_grosze: int = 0
    free: bool = False
    card_mode: str = "simple"  # "simple" (default, kolokwium) or "detailed" (egzamin)
    quality_pass: bool = False  # adds a Claude Opus 4.7 review pass post-Haiku


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = Lock()


def _recover_jobs_from_disk() -> None:
    """At startup, re-register any `<jobid>.cards.json` files in WORK_DIR as
    completed jobs. Keeps already-generated decks reachable via /study/<id>
    across server restarts — avoids the operator paying API again just to
    re-view what's already on disk."""
    for cards_path in WORK_DIR.glob("*.cards.json"):
        job_id = cards_path.stem.removesuffix(".cards")
        deck_path = WORK_DIR / f"{job_id}.apkg"
        if not deck_path.exists():
            continue
        job = Job(
            job_id=job_id,
            pdf_name=f"recovered-{job_id}.pdf",
            pdf_pages=0,
            status="done",
            cards_path=cards_path,
            deck_path=deck_path,
            paid=True,
            free=True,  # disk-recovered jobs bypass payment (already paid for once)
        )
        with _JOBS_LOCK:
            _JOBS[job_id] = job


def _new_job(pdf_name: str, pdf_pages: int, quality_pass: bool = False) -> Job:
    jid = uuid.uuid4().hex[:12]
    job = Job(job_id=jid, pdf_name=pdf_name, pdf_pages=pdf_pages,
              price_grosze=price_for_pages(pdf_pages, quality_pass=quality_pass),
              quality_pass=quality_pass)
    with _JOBS_LOCK:
        _JOBS[jid] = job
    return job


def _get_job(jid: str) -> Job:
    with _JOBS_LOCK:
        job = _JOBS.get(jid)
    if not job:
        raise HTTPException(404, "Nie znaleziono zadania (job not found or expired).")
    return job


def _purge_expired() -> None:
    now = time.time()
    with _JOBS_LOCK:
        stale = [j for j in _JOBS.values() if now - j.created_at > JOB_TTL_SECONDS]
    for j in stale:
        for p in (j.pdf_path, j.deck_path, j.cards_path):
            try:
                if p and p.exists():
                    p.unlink()
            except OSError:
                pass
        with _JOBS_LOCK:
            _JOBS.pop(j.job_id, None)


# ---------- pipeline runner ----------


def _run_job_sync(job: Job) -> None:
    job.status = "running"
    job.log_lines.append(f"Start: {job.pdf_name}")
    try:
        deck_path = WORK_DIR / f"{job.job_id}.apkg"
        dev_max = os.environ.get("FISZKOMAT_DEV_MAX_CHUNKS")
        stats = core_run(
            pdf_path=job.pdf_path,
            out_path=deck_path,
            pages_per_chunk=5,
            dry_run=False,
            max_chunks=int(dev_max) if dev_max else None,
            log=lambda s: job.log_lines.append(s),
            card_mode=job.card_mode,
            quality_pass=job.quality_pass,
        )
        job.stats = stats
        job.deck_path = deck_path
        job.cards_path = deck_path.with_suffix(".cards.json")
        job.status = "done"
        job.finished_at = time.time()
    except EmptyPdfError as e:
        # User-facing problem (scanned PDF, no extractable text). Don't dump a
        # stack trace — the message itself is the actionable feedback.
        job.error = str(e)
        job.status = "failed"
        job.finished_at = time.time()
    except Exception as e:
        job.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        job.status = "failed"
        job.finished_at = time.time()
    finally:
        if job.pdf_path and job.pdf_path.exists():
            try:
                job.pdf_path.unlink()
            except OSError:
                pass
            job.pdf_path = None


def _kick_off_generation(job: Job) -> None:
    if job.status in ("queued", "running", "done"):
        return  # idempotent — webhook + redirect may both fire
    if not job.pdf_path or not job.pdf_path.exists():
        job.status = "failed"
        job.error = "PDF już nie istnieje (oczekiwanie na płatność przekroczyło czas)."
        return
    job.status = "queued"
    asyncio.get_event_loop().run_in_executor(None, _run_job_sync, job)


# ---------- FastAPI app ----------


app = FastAPI(title="fiszkomat", version="0.1.0")


INDEX_HTML = """<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>fiszkomat — skrypt → Anki</title>
<style>
:root {
  --bg:#f4ede0;--bg-paper:#faf6ec;--ink:#1a1814;--ink-soft:#4a443a;--ink-muted:#8a8275;
  --rule:#c9bfa8;--accent:#7a1f1f;--accent-soft:#a83a3a;--gold:#a87b3a;--green:#3a5a3a;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--ink);font-family:Inter,system-ui,Arial,sans-serif;
     min-height:100vh;padding:32px 24px 80px;line-height:1.5;}
.wrap{max-width:680px;margin:0 auto}
header{border-bottom:2px solid var(--ink);padding-bottom:16px;margin-bottom:16px;}
.eyebrow{font-family:JetBrains Mono,monospace;font-size:11px;letter-spacing:.18em;
         text-transform:uppercase;color:var(--ink-muted);margin-bottom:4px;}
h1{font-family:Cormorant Garamond,Georgia,serif;font-weight:600;font-size:48px;line-height:1;
   letter-spacing:-.02em;}
h1 em{font-style:italic;color:var(--accent);}
.wordmark-link{text-decoration:none;color:inherit;display:inline-block;transition:opacity .15s ease;}
.wordmark-link:hover{opacity:.75;}
.wordmark-link:hover h1 em{color:var(--accent-soft);}
.subtitle{font-family:Cormorant Garamond,Georgia,serif;font-style:italic;font-size:16px;
          color:var(--ink-soft);border-top:1px solid var(--rule);padding-top:8px;margin-bottom:32px;}
.card{background:var(--bg-paper);padding:24px;border:1px solid var(--rule);}
label{display:block;font-family:JetBrains Mono,monospace;font-size:11px;letter-spacing:.18em;
      text-transform:uppercase;color:var(--gold);margin-bottom:8px;}
input[type=file]{display:block;width:100%;padding:14px;background:#fff;border:1px dashed var(--rule);
                 font-family:inherit;font-size:14px;color:var(--ink-soft);cursor:pointer;}
button{margin-top:18px;padding:12px 24px;background:var(--accent);color:#fff;border:none;
       font-family:JetBrains Mono,monospace;font-size:12px;letter-spacing:.15em;text-transform:uppercase;
       cursor:pointer;}
button:hover{background:var(--accent-soft);}
button:disabled{background:var(--ink-muted);cursor:not-allowed;}
.muted{color:var(--ink-muted);font-size:13px;margin-top:12px;font-style:italic;font-family:Cormorant Garamond,Georgia,serif;}
.formats{margin-top:20px;padding:16px 18px;background:#fff;border:1px solid var(--rule);
         border-left:3px solid var(--green);}
.formats-eyebrow{font-family:JetBrains Mono,monospace;font-size:11px;letter-spacing:.18em;
                 text-transform:uppercase;color:var(--ink-muted);margin-bottom:10px;}
.formats ul{list-style:none;padding:0;margin:0 0 8px;}
.formats li{font-family:'Cormorant Garamond',Georgia,serif;font-size:14.5px;line-height:1.55;
            color:var(--ink-soft);padding:3px 0;}
.formats li b{font-style:normal;font-weight:600;color:var(--ink);font-family:inherit;}
.formats-note{font-family:JetBrains Mono,monospace;font-size:10px;letter-spacing:.05em;
              color:var(--ink-muted);padding-top:8px;border-top:1px solid var(--rule);}
.price{margin-top:14px;padding:10px 14px;background:#fff;border-left:3px solid var(--gold);
       font-family:JetBrains Mono,monospace;font-size:12px;color:var(--ink-soft);}
.price b{color:var(--ink);}
#log{margin-top:24px;padding:18px;background:#fff;border:1px solid var(--rule);
     font-family:JetBrains Mono,monospace;font-size:11px;color:var(--ink-soft);
     white-space:pre-wrap;max-height:280px;overflow:auto;display:none;}
.status{display:none;justify-content:space-between;align-items:center;margin-top:18px;
        font-family:JetBrains Mono,monospace;font-size:11px;letter-spacing:.1em;color:var(--ink-muted);
        text-transform:uppercase;}
.status.show{display:flex;}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--gold);margin-right:8px;
     animation:pulse 1.2s ease-in-out infinite;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.3}}
a.dl{display:inline-block;margin-top:18px;padding:12px 24px;background:var(--green);color:#fff;
     text-decoration:none;font-family:JetBrains Mono,monospace;font-size:12px;letter-spacing:.15em;
     text-transform:uppercase;}
a.dl:hover{background:#4c6e4c;}
a.dl.primary{background:var(--green);}
a.dl.secondary{background:var(--gold);margin-top:8px;}
a.dl.secondary:hover{background:#946a32;}
@media (min-width:520px){
  a.dl.primary{margin-right:8px;}
  a.dl.secondary{margin-top:0;}
}
footer{margin-top:36px;font-family:JetBrains Mono,monospace;font-size:10px;color:var(--ink-muted);
       letter-spacing:.1em;}
.sublabel{margin-top:16px;}
.mode-row{display:flex;gap:8px;flex-direction:column;margin-bottom:8px;}
@media (min-width:520px){.mode-row{flex-direction:row;}}
.mode-opt{flex:1;display:block;cursor:pointer;padding:12px 14px;background:#fff;
          border:1px solid var(--rule);font-family:inherit;letter-spacing:normal;
          text-transform:none;color:var(--ink-soft);}
.mode-opt input{margin-right:8px;vertical-align:middle;}
.mode-opt:hover{background:#fdf9f0;}
.mode-opt:has(input:checked){border-color:var(--accent);background:#fff;color:var(--ink);}
.mode-title{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;font-size:18px;
            color:var(--ink);display:inline;}
.mode-desc{display:block;margin-top:4px;font-style:italic;font-family:'Cormorant Garamond',Georgia,serif;
           font-size:13px;color:var(--ink-muted);}

/* ---- Variant modal (post-Generate-click) ---- */
.variant-modal{position:fixed;inset:0;z-index:100;display:flex;align-items:center;
               justify-content:center;padding:16px;}
.variant-modal[hidden]{display:none !important;}
.variant-modal-backdrop{position:absolute;inset:0;background:rgba(26,24,20,.55);
                        backdrop-filter:blur(2px);}
.variant-modal-content{position:relative;max-width:520px;width:100%;max-height:calc(100vh - 32px);
                       overflow:auto;background:var(--bg-paper);border:1px solid var(--ink);
                       padding:28px 24px;display:flex;flex-direction:column;gap:14px;
                       box-shadow:0 12px 48px rgba(0,0,0,.35);}
.variant-modal-eyebrow{font-family:JetBrains Mono,monospace;font-size:11px;letter-spacing:.18em;
                       text-transform:uppercase;color:var(--ink-muted);}
.variant-modal-h2{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;font-size:28px;
                  line-height:1.05;color:var(--ink);margin:2px 0 8px;letter-spacing:-.01em;}
.variant-modal-h2 em{font-style:italic;color:var(--accent);}
.variant-option{position:relative;display:block;width:100%;text-align:left;
                background:#fff;border:1px solid var(--rule);padding:18px 20px;cursor:pointer;
                font-family:inherit;letter-spacing:normal;text-transform:none;color:inherit;
                margin-top:0;transition:border-color .15s ease, box-shadow .15s ease;}
.variant-option:hover{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent);}
.variant-option.is-premium{border-color:var(--gold);}
.variant-option.is-premium:hover{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent);}
.variant-option-badge{position:absolute;top:-1px;right:-1px;background:var(--gold);color:#fff;
                      padding:3px 10px;font-family:JetBrains Mono,monospace;font-size:9px;
                      letter-spacing:.18em;text-transform:uppercase;}
.variant-option-name{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;font-size:20px;
                     line-height:1.1;color:var(--ink);}
.variant-option-desc{margin-top:8px;font-family:'Cormorant Garamond',Georgia,serif;
                     font-style:italic;font-size:14px;line-height:1.45;color:var(--ink-soft);}
.variant-option-price{margin-top:12px;font-family:JetBrains Mono,monospace;font-size:14px;
                      color:var(--ink);font-weight:600;letter-spacing:.02em;}
.variant-option-price-note{font-weight:400;font-size:11px;color:var(--ink-muted);
                           letter-spacing:.04em;margin-left:6px;}
.variant-option-cta{margin-top:6px;font-family:JetBrains Mono,monospace;font-size:10px;
                    letter-spacing:.18em;text-transform:uppercase;color:var(--accent);}
.variant-option-cta .arrow{font-size:13px;}
.variant-modal-cancel{margin-top:4px;background:transparent;color:var(--ink-muted);border:none;
                      padding:10px;cursor:pointer;font-family:JetBrains Mono,monospace;
                      font-size:10px;letter-spacing:.18em;text-transform:uppercase;
                      align-self:center;}
.variant-modal-cancel:hover{color:var(--ink);}

/* ---- FISZKI PRZYKŁADOWE ---- */
.samples{margin-top:56px;}
.samples-eyebrow{font-family:JetBrains Mono,monospace;font-size:11px;letter-spacing:.18em;
                 text-transform:uppercase;color:var(--ink-muted);margin-bottom:6px;}
.samples-h2{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;font-size:32px;
            line-height:1.05;letter-spacing:-.02em;color:var(--ink);}
.samples-h2 em{font-style:italic;color:var(--accent);}
.samples-sub{font-family:'Cormorant Garamond',Georgia,serif;font-style:italic;font-size:15px;
             color:var(--ink-soft);border-top:1px solid var(--rule);padding-top:8px;margin-top:6px;
             margin-bottom:16px;}
.samples-meta{font-family:JetBrains Mono,monospace;font-size:11px;color:var(--ink-muted);
              letter-spacing:.05em;margin-bottom:20px;}
.samples-meta b{color:var(--ink);font-weight:600;}
/* Deck library — responsive grid + list views, user-togglable. */
.view-toggle{display:inline-flex;gap:0;margin-bottom:18px;border:1px solid var(--rule);
             background:#fff;}
.view-toggle-btn{margin-top:0;padding:8px 14px;background:transparent;color:var(--ink-muted);
                 border:none;cursor:pointer;font-family:JetBrains Mono,monospace;
                 font-size:10px;letter-spacing:.18em;text-transform:uppercase;
                 transition:background .15s ease, color .15s ease;}
.view-toggle-btn:hover{background:#fdf9f0;color:var(--ink);}
.view-toggle-btn.is-active{background:var(--ink);color:#fff;}
.view-toggle-icon{margin-right:6px;font-size:13px;letter-spacing:0;}

/* Grid view (default). 1 col mobile / 2 col tablet+.
   `[hidden]` rule explicit because `display: grid/flex` overrides the
   browser-default `display: none` for the HTML hidden attribute. */
.deck-grid[hidden], .deck-list[hidden]{display:none !important;}
.deck-grid{display:grid;gap:14px;grid-template-columns:1fr;margin-bottom:22px;}
@media (min-width:520px){.deck-grid{grid-template-columns:1fr 1fr;}}
.deck-tile{display:flex;flex-direction:column;gap:8px;padding:20px 18px 18px;
           background:var(--bg-paper);border:1px solid var(--rule);
           transition:border-color .15s ease, box-shadow .15s ease;}
.deck-tile:hover{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent);}
.deck-tile-num{font-family:JetBrains Mono,monospace;font-size:10px;letter-spacing:.20em;
               text-transform:uppercase;color:var(--accent);}
.deck-tile-title{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;
                 font-size:22px;line-height:1.1;letter-spacing:-.01em;color:var(--ink);
                 margin:0;}
.deck-tile-sub{font-family:'Cormorant Garamond',Georgia,serif;font-style:italic;
               font-size:14px;line-height:1.4;color:var(--ink-soft);flex:1;}
.deck-tile-meta{font-family:JetBrains Mono,monospace;font-size:10px;letter-spacing:.06em;
                color:var(--ink-muted);text-transform:uppercase;}
.deck-tile-meta b{color:var(--ink);font-weight:600;}
.deck-tile-actions{display:flex;align-items:baseline;justify-content:space-between;gap:14px;
                   margin-top:8px;padding-top:14px;border-top:1px solid var(--rule);
                   flex-wrap:wrap;}
.deck-tile-cta{display:inline-flex;align-items:center;gap:8px;padding:10px 18px;
               background:var(--accent);color:#fff;text-decoration:none;
               font-family:JetBrains Mono,monospace;font-size:11px;letter-spacing:.15em;
               text-transform:uppercase;transition:background .15s ease;}
.deck-tile-cta:hover{background:var(--accent-soft);}
.deck-tile-cta .arrow{font-size:13px;}
.deck-tile-apkg{font-family:'Cormorant Garamond',Georgia,serif;font-style:italic;font-size:13px;
                color:var(--ink-muted);text-decoration:underline;text-underline-offset:3px;
                text-decoration-color:var(--rule);white-space:nowrap;}
.deck-tile-apkg:hover{color:var(--ink-soft);text-decoration-color:var(--gold);}

/* List view (alternative). Compact rows for scan-first users. */
.deck-list{display:flex;flex-direction:column;gap:0;margin-bottom:22px;
           background:var(--bg-paper);border:1px solid var(--rule);}
.deck-row{display:grid;grid-template-columns:auto 1fr auto auto;gap:14px;align-items:baseline;
          padding:14px 18px;border-bottom:1px solid var(--rule);text-decoration:none;
          color:inherit;transition:background .15s ease;}
.deck-row:last-child{border-bottom:none;}
.deck-row:hover{background:#fdf9f0;}
.deck-row-num{font-family:JetBrains Mono,monospace;font-size:10px;letter-spacing:.18em;
              text-transform:uppercase;color:var(--accent);min-width:62px;}
.deck-row-title{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;font-size:18px;
                color:var(--ink);line-height:1.2;}
.deck-row-title small{display:block;font-family:'Cormorant Garamond',Georgia,serif;
                      font-style:italic;font-weight:400;font-size:13px;color:var(--ink-muted);
                      margin-top:2px;}
.deck-row-count{font-family:JetBrains Mono,monospace;font-size:11px;color:var(--ink-muted);
                white-space:nowrap;}
.deck-row-arrow{font-size:18px;color:var(--accent);font-family:JetBrains Mono,monospace;}
.deck-row:hover .deck-row-arrow{transform:translateX(2px);transition:transform .15s ease;}
@media (max-width:520px){
  .deck-row{grid-template-columns:auto 1fr auto;}
  .deck-row-count{grid-column:2;font-size:10px;}
  .deck-row-arrow{grid-row:1/3;}
}

/* ---- kontakt ---- */
.contact{margin-top:48px;padding:20px 22px;background:var(--bg-paper);
         border:1px solid var(--rule);border-left:3px solid var(--gold);}
.contact-eyebrow{font-family:JetBrains Mono,monospace;font-size:11px;letter-spacing:.18em;
                 text-transform:uppercase;color:var(--ink-muted);margin-bottom:8px;}
.contact-line{font-family:'Cormorant Garamond',Georgia,serif;font-style:italic;font-size:17px;
              color:var(--ink-soft);line-height:1.45;}
.contact-mail{color:var(--accent);text-decoration:underline;text-underline-offset:3px;
              text-decoration-color:rgba(122,31,31,.4);font-style:normal;
              font-family:'JetBrains Mono',monospace;font-size:15px;}
.contact-mail:hover{text-decoration-color:var(--accent);}

.samples-grid{display:grid;grid-template-columns:1fr;gap:12px;}
@media (min-width:520px){.samples-grid{grid-template-columns:1fr 1fr;}}
.sample-card{perspective:1400px;cursor:pointer;min-height:200px;}
.sample-face-wrap{position:relative;width:100%;height:100%;min-height:200px;
                  transform-style:preserve-3d;
                  transition:transform .5s cubic-bezier(.4,0,.2,1);}
.sample-card.is-flipped .sample-face-wrap{transform:rotateY(180deg);}
.sample-face{position:absolute;inset:0;padding:18px 18px 30px;background:var(--bg-paper);
             border:1px solid var(--rule);backface-visibility:hidden;
             -webkit-backface-visibility:hidden;overflow:hidden;display:flex;flex-direction:column;}
.sample-face.back{transform:rotateY(180deg);background:#fff;}
.sample-z{font-family:JetBrains Mono,monospace;font-size:10px;letter-spacing:.18em;
          text-transform:uppercase;color:var(--ink-muted);margin-bottom:6px;}
.sample-title{font-family:'Cormorant Garamond',Georgia,serif;font-weight:600;font-size:22px;
              line-height:1.1;color:var(--accent);letter-spacing:-.01em;margin-bottom:8px;}
.sample-drugs{font-family:Inter,system-ui,sans-serif;font-style:italic;font-size:14px;
              color:var(--ink-soft);line-height:1.45;flex:1;}
.sample-flip-hint{position:absolute;bottom:8px;right:12px;font-family:JetBrains Mono,monospace;
                  font-size:9px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-muted);
                  opacity:.6;}
.sample-section{margin-bottom:10px;}
.sample-section:last-child{margin-bottom:0;}
.sample-section-label{font-family:JetBrains Mono,monospace;font-size:9px;letter-spacing:.18em;
                      text-transform:uppercase;color:var(--gold);margin-bottom:3px;}
.sample-section-text{font-family:Inter,system-ui,sans-serif;font-size:13px;color:var(--ink);
                     line-height:1.5;}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div class="eyebrow">/// flashcards from your skrypt</div>
  <a href="/" class="wordmark-link" title="Strona główna"><h1>fiszk<em>o</em>mat</h1></a>
</header>
<div class="subtitle">PDF skryptu → talia Anki. Po polsku, bez głupienia po 10 stronie.</div>

<div class="card">
  <form id="f">
    <label for="pdf">Wgraj skrypt (PDF, max 30 MB, max 500 stron)</label>
    <input id="pdf" type="file" name="pdf" accept="application/pdf" required>
    <label class="sublabel">Tryb fiszek</label>
    <div class="mode-row">
      <label class="mode-opt">
        <input type="radio" name="mode" value="simple" checked>
        <span class="mode-title">Uproszczone</span>
        <span class="mode-desc">Krócej: grupa, mechanizm, wskazania. Do kolokwium.</span>
      </label>
      <label class="mode-opt">
        <input type="radio" name="mode" value="detailed">
        <span class="mode-title">Szczegółowe</span>
        <span class="mode-desc">Pełne: + przeciwwskazania + działania niepożądane. Do egzaminu.</span>
      </label>
    </div>
    <div class="price">
      Cennik wg liczby stron:
      <b>3 zł</b> do 50 s. ·
      <b>5 zł</b> 51–150 s. ·
      <b>10 zł</b> 151–300 s. ·
      <b>15 zł</b> 301–500 s.
      Płatność jednorazowa kartą (Stripe).
    </div>
    <input type="hidden" name="quality_pass" id="quality_pass" value="false">
    <button id="go" type="submit">Wygeneruj fiszki</button>
    <div class="muted">Generowanie 30-stronicowego skryptu trwa około 4 minuty. Plik PDF jest usuwany od razu po wygenerowaniu talii.</div>
  </form>

  <div class="formats">
    <div class="formats-eyebrow">/// obsługujemy</div>
    <ul>
      <li><b>PDF natywny</b> — z warstwą tekstową (e-book, eksport z Worda, większość skryptów).</li>
      <li><b>PDF skanowany</b> — automatyczny OCR przez Claude vision (wolniejsze).</li>
      <li><b>PDF z popsutą warstwą tekstową</b> — wykrywane i przekierowane na ten sam tor OCR.</li>
    </ul>
    <div class="formats-note">Polski tekst. Max 30 MB, max 500 stron. Inne języki — w planach.</div>
  </div>
  <div id="status" class="status"><span><span class="dot"></span><span id="statusText">w kolejce</span></span><span id="elapsed"></span></div>
  <pre id="log"></pre>
  <div id="download"></div>
</div>

<!-- Variant picker modal — shown after Wygeneruj fiszki click. -->
<div id="variantModal" class="variant-modal" hidden>
  <div class="variant-modal-backdrop"></div>
  <div class="variant-modal-content">
    <div class="variant-modal-eyebrow">/// wybierz wariant</div>
    <h2 class="variant-modal-h2">Jak zrobić <em>tę talię</em>?</h2>

    <button type="button" class="variant-option" data-quality="false">
      <div class="variant-option-name">Standardowy</div>
      <div class="variant-option-desc">
        Generowanie przez Haiku 4.5 — chunkowanie po nagłówku „Zajęcia",
        walidacja schematu, deduplikacja. Tym samym pipeline'em zrobione są
        fiszki przykładowe powyżej.
      </div>
      <div class="variant-option-price">3 – 15 zł <span class="variant-option-price-note">(zależnie od długości)</span></div>
      <div class="variant-option-cta">Wybierz <span class="arrow">→</span></div>
    </button>

    <button type="button" class="variant-option is-premium" data-quality="true">
      <div class="variant-option-badge">polecane</div>
      <div class="variant-option-name">Standardowy + quality pass</div>
      <div class="variant-option-desc">
        Wszystko ze standardowego, plus drugi przebieg przez Claude 4.7
        (Opus) — recenzent farmakologiczny wyłapuje pomieszane grupy
        leków, błędne mechanizmy i nieprawidłowe wskazania, koryguje karty
        w miejscu.
      </div>
      <div class="variant-option-price">5 – 25 zł <span class="variant-option-price-note">(zależnie od długości)</span></div>
      <div class="variant-option-cta">Wybierz <span class="arrow">→</span></div>
    </button>

    <button type="button" class="variant-modal-cancel">Anuluj</button>
  </div>
</div>

<!--SAMPLES-->

<section class="contact">
  <div class="contact-eyebrow">/// kontakt</div>
  <div class="contact-line">
    Chcesz, żeby na stronie pojawiła się talia z innych zajęć? Napisz:
    <a class="contact-mail" href="mailto:kontakt@ewwesolutions.work">kontakt@ewwesolutions.work</a>
  </div>
</section>

<footer>fiszkomat v0.1.0 · Phase 1 · Haiku 4.5</footer>
</div>

<script>
const f = document.getElementById('f');
const log = document.getElementById('log');
const status = document.getElementById('status');
const statusText = document.getElementById('statusText');
const elapsed = document.getElementById('elapsed');
const download = document.getElementById('download');
const go = document.getElementById('go');

async function pollUntilDone(job_id) {
  let lastLen = 0;
  const t0 = Date.now();
  let timer = setInterval(() => {
    elapsed.textContent = Math.floor((Date.now()-t0)/1000) + 's';
  }, 200);
  while (true) {
    await new Promise(r => setTimeout(r, 2000));
    const s = await fetch(`/jobs/${job_id}`).then(r => r.json());
    log.textContent = (s.log_lines || []).join('\\n');
    log.scrollTop = log.scrollHeight;
    if (s.status === 'done') {
      statusText.textContent = 'gotowe';
      clearInterval(timer);
      const stats = s.stats || {};
      download.innerHTML = `<a class="dl primary" href="/study/${job_id}">Studiuj online (wersja mobilna)</a>
        <a class="dl secondary" href="/jobs/${job_id}/deck">Pobierz .apkg (Anki Desktop)</a>
        <div class="muted">${stats.cards_valid} fiszek · ${Math.round(stats.wall_seconds||0)}s</div>`;
      go.disabled = false;
      return;
    }
    if (s.status === 'failed') {
      statusText.textContent = 'błąd';
      log.textContent += '\\n\\nBŁĄD:\\n' + (s.error || '');
      clearInterval(timer);
      go.disabled = false;
      return;
    }
    statusText.textContent = s.status === 'queued' ? 'w kolejce' : (s.status === 'running' ? 'generowanie' : s.status);
  }
}

// Resume polling if we returned from Stripe with ?job=<id>
const resumeJob = new URLSearchParams(window.location.search).get('job');
if (resumeJob) {
  log.style.display = 'block';
  status.classList.add('show');
  statusText.textContent = 'płatność zaakceptowana';
  pollUntilDone(resumeJob);
}

// ---- Variant modal: shown after Generate click, user picks Standard vs Quality pass.
const variantModal = document.getElementById('variantModal');
const variantOptions = variantModal.querySelectorAll('.variant-option');
const variantBackdrop = variantModal.querySelector('.variant-modal-backdrop');
const variantCancel = variantModal.querySelector('.variant-modal-cancel');
const qualityField = document.getElementById('quality_pass');

function openVariantModal() {
  variantModal.hidden = false;
  document.body.style.overflow = 'hidden';
}
function closeVariantModal() {
  variantModal.hidden = true;
  document.body.style.overflow = '';
}
function cancelGenerate() {
  closeVariantModal();
  go.disabled = false;
  log.style.display = 'none';
  status.classList.remove('show');
}
variantCancel.addEventListener('click', cancelGenerate);
variantBackdrop.addEventListener('click', cancelGenerate);
document.addEventListener('keydown', (e) => {
  if (!variantModal.hidden && e.key === 'Escape') cancelGenerate();
});

variantOptions.forEach((opt) => {
  opt.addEventListener('click', async () => {
    qualityField.value = opt.dataset.quality === 'true' ? 'true' : 'false';
    closeVariantModal();
    await submitGenerationForm();
  });
});

async function submitGenerationForm() {
  const fd = new FormData(f);
  let res;
  try {
    res = await fetch('/jobs', {method:'POST', body: fd});
  } catch (err) {
    statusText.textContent = 'błąd sieci';
    go.disabled = false;
    return;
  }
  if (!res.ok) {
    statusText.textContent = 'błąd';
    log.textContent = await res.text();
    go.disabled = false;
    return;
  }
  const data = await res.json();
  if (data.checkout_url) {
    statusText.textContent = 'przekierowanie do płatności…';
    window.location.href = data.checkout_url;
    return;
  }
  // Free path — job already kicked off
  statusText.textContent = 'w kolejce';
  pollUntilDone(data.job_id);
}

// Form submit just shows the variant picker. The actual POST happens when
// the user picks an option in the modal (or cancels).
f.addEventListener('submit', (e) => {
  e.preventDefault();
  // Validate file selected client-side before opening modal — saves a server roundtrip.
  if (!f.pdf.files || !f.pdf.files.length) {
    return;
  }
  go.disabled = true;
  download.innerHTML = '';
  log.textContent = '';
  openVariantModal();
});
</script>
</body>
</html>
"""


# Curated sample decks shown on the landing page. Each slug maps to a
# `test_docs/out/<slug>.cards.json` file. The titles aren't derivable from
# card content (cards carry group titles like "Antagoniści receptorów
# muskarynowych", not skrypt-level titles), so they're hardcoded here.
SAMPLE_DECKS: list[dict] = [
    {
        "slug": "zaj08",
        "title": "Układ oddechowy",
        "zajecia": 8,
        "subtitle": "Farmakologia układu oddechowego",
    },
    {
        "slug": "zaj13",
        "title": "Toksykologia",
        "zajecia": 13,
        "subtitle": "Mechanizmy działania trucizn i postępowanie w zatruciach",
    },
    {
        "slug": "zaj15",
        "title": "Antybiotyki i środki odkażające",
        "zajecia": 15,
        "subtitle": "Chemioterapia przeciwbakteryjna — antyseptyki, penicyliny, cefalosporyny, karbapenemy, aminoglikozydy, makrolidy, fluorochinolony, tuberkulostatyki, leki w trądzie",
    },
    {
        "slug": "zaj16",
        "title": "Leki przeciwwirusowe, przeciwgrzybicze i przeciwpasożytnicze",
        "zajecia": 16,
        "subtitle": "HSV/CMV/HIV/HBV/HCV/grypa/RSV/SARS-CoV-2, polieny + azole + echinokandyny, malaria, robaki",
    },
    {
        "slug": "zaj17",
        "title": "Leki układu pokarmowego",
        "zajecia": 17,
        "subtitle": "Farmakologia układu pokarmowego",
    },
    {
        "slug": "zaj18",
        "title": "Hormony — przysadka, tarczyca, nadnercza, płciowe",
        "zajecia": 18,
        "subtitle": "Rozdział 16: GH, somatostatyny, GnRH, gonadotropiny, hormony tarczycy + tyreostatyki, GKS i mineralokortykosteroidy, hormony płciowe, antykoncepcja",
    },
    {
        "slug": "zaj19",
        "title": "Metabolizm wapnia, cukrzyca, otyłość",
        "zajecia": 19,
        "subtitle": "Hormony i leki rozdziałów 16.5 / 16.6 / 16.7: metabolizm kości i wapnia, leki przeciwcukrzycowe, leki w otyłości",
    },
]


def _samples_dir() -> Path:
    # web.py → src/fiszkomat/web.py, so parent.parent.parent = project root
    return Path(__file__).parent.parent.parent / "test_docs" / "out"


def _load_sample_deck(slug: str) -> list[dict]:
    """Load cards for one curated sample slug. Returns [] if missing/empty."""
    path = _samples_dir() / f"{slug}.cards.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [c for c in data if isinstance(c, dict)]


def _load_all_sample_decks() -> list[dict]:
    """Return SAMPLE_DECKS enriched with loaded cards. Decks with no cards
    on disk are filtered out, so a partial checkout (only zaj13 generated,
    say) just renders one tab instead of two empty ones."""
    decks: list[dict] = []
    for meta in SAMPLE_DECKS:
        cards = _load_sample_deck(meta["slug"])
        if cards:
            decks.append({**meta, "cards": cards})
    return decks


def _render_one_card(c: dict) -> str:
    """Render a single flip-card. Used both for the per-deck grid on the
    landing and (in a slightly different wrapper) elsewhere if needed."""
    e = html_lib.escape
    t = e(str(c.get("t", "")))
    d = e(str(c.get("d", "")))
    m = e(str(c.get("m", "")))
    i = e(str(c.get("i", "")))
    ck = e(str(c.get("c", "")))
    return f'''<div class="sample-card" onclick="this.classList.toggle('is-flipped')">
  <div class="sample-face-wrap">
    <div class="sample-face front">
      <div class="sample-title">{t}</div>
      <div class="sample-drugs">{d}</div>
      <div class="sample-flip-hint">odwróć ⟳</div>
    </div>
    <div class="sample-face back">
      <div class="sample-section">
        <div class="sample-section-label">Mechanizm</div>
        <div class="sample-section-text">{m}</div>
      </div>
      <div class="sample-section">
        <div class="sample-section-label">Wskazania</div>
        <div class="sample-section-text">{i}</div>
      </div>
      <div class="sample-section">
        <div class="sample-section-label">Przeciwwskazania</div>
        <div class="sample-section-text">{ck}</div>
      </div>
      <div class="sample-flip-hint">odwróć ⟳</div>
    </div>
  </div>
</div>'''


def _render_samples_html(decks: list[dict]) -> str:
    """Render the deck library in TWO togglable views — grid (default) and
    list. Both renders are emitted to the DOM; CSS via the `display`
    property on `.deck-grid` / `.deck-list` controls which is visible,
    swapped by the toggle JS. User's preference persists in localStorage.

    Returns "" if no decks have cards on disk."""
    if not decks:
        return ""
    e = html_lib.escape
    total = sum(len(d["cards"]) for d in decks)

    tiles_html: list[str] = []
    rows_html: list[str] = []
    for deck in decks:
        slug = deck["slug"]
        title = e(deck["title"])
        zajecia = deck["zajecia"]
        subtitle = e(deck["subtitle"])
        n_cards = len(deck["cards"])

        # Grid tile
        tiles_html.append(
            f'<article class="deck-tile">'
            f'<div class="deck-tile-num">Zajęcia {zajecia}</div>'
            f'<h3 class="deck-tile-title">{title}</h3>'
            f'<p class="deck-tile-sub">{subtitle}</p>'
            f'<div class="deck-tile-meta"><b>{n_cards}</b> fiszek · format Anki</div>'
            f'<div class="deck-tile-actions">'
            f'<a class="deck-tile-cta" href="/study/sample/{slug}">'
            f'Studiuj <span class="arrow">→</span>'
            f'</a>'
            f'<a class="deck-tile-apkg" href="/sample/{slug}/deck">.apkg</a>'
            f'</div>'
            f'</article>'
        )

        # List row — whole row is clickable to /study/sample/<slug>
        rows_html.append(
            f'<a class="deck-row" href="/study/sample/{slug}">'
            f'<span class="deck-row-num">Z{zajecia}</span>'
            f'<span class="deck-row-title">{title}<small>{subtitle}</small></span>'
            f'<span class="deck-row-count">{n_cards} fiszek</span>'
            f'<span class="deck-row-arrow">→</span>'
            f'</a>'
        )

    return (
        '<section class="samples">'
        '<div class="samples-eyebrow">/// fiszki przykładowe</div>'
        '<h2 class="samples-h2">Tak wyglądają <em>gotowe</em> fiszki.</h2>'
        '<div class="samples-sub">Wybierz talię i przejdź do trybu nauki.</div>'
        f'<div class="samples-meta"><b>{total}</b> fiszek w {len(decks)} taliach</div>'
        '<div class="view-toggle" role="tablist" aria-label="Widok talii">'
        '  <button type="button" class="view-toggle-btn" data-view="grid" role="tab" aria-selected="true">'
        '    <span class="view-toggle-icon">▦</span>Siatka'
        '  </button>'
        '  <button type="button" class="view-toggle-btn" data-view="list" role="tab" aria-selected="false">'
        '    <span class="view-toggle-icon">☰</span>Lista'
        '  </button>'
        '</div>'
        '<div class="deck-grid" data-view="grid">' + "".join(tiles_html) + '</div>'
        '<div class="deck-list" data-view="list" hidden>' + "".join(rows_html) + '</div>'
        '</section>'
        '<script>(function(){\n'
        '  var KEY = "fiszkomat-samples-view";\n'
        '  var btns = document.querySelectorAll(".view-toggle-btn");\n'
        '  var views = {\n'
        '    grid: document.querySelector(".deck-grid"),\n'
        '    list: document.querySelector(".deck-list")\n'
        '  };\n'
        '  function set(v) {\n'
        '    if (!views[v]) v = "grid";\n'
        '    Object.keys(views).forEach(function(k){\n'
        '      if (views[k]) views[k].hidden = (k !== v);\n'
        '    });\n'
        '    btns.forEach(function(b){\n'
        '      var active = b.getAttribute("data-view") === v;\n'
        '      b.classList.toggle("is-active", active);\n'
        '      b.setAttribute("aria-selected", active ? "true" : "false");\n'
        '    });\n'
        '    try { localStorage.setItem(KEY, v); } catch(e) {}\n'
        '  }\n'
        '  var initial = "grid";\n'
        '  try { initial = localStorage.getItem(KEY) || "grid"; } catch(e) {}\n'
        '  set(initial);\n'
        '  btns.forEach(function(b){\n'
        '    b.addEventListener("click", function(){ set(b.getAttribute("data-view")); });\n'
        '  });\n'
        '})();</script>'
    )


# Pre-render once at module import. Operator drops new `<slug>.cards.json`
# files into test_docs/out/, restarts fiszkomat, new decks appear.
_SAMPLE_DECKS_LOADED: list[dict] = _load_all_sample_decks()
_SAMPLES_HTML: str = _render_samples_html(_SAMPLE_DECKS_LOADED)
INDEX_HTML = INDEX_HTML.replace("<!--SAMPLES-->", _SAMPLES_HTML)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    _purge_expired()
    return INDEX_HTML


@app.post("/jobs")
async def create_job(request: Request, pdf: UploadFile = File(...),
                      mode: str = Form("simple"),
                      quality_pass: str = Form("false")) -> JSONResponse:
    if (pdf.content_type or "").lower() not in {"application/pdf", "application/x-pdf"}:
        raise HTTPException(400, "Wymagany plik PDF (Content-Type: application/pdf).")
    if not (pdf.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Plik musi mieć rozszerzenie .pdf.")

    data = await pdf.read(MAX_PDF_BYTES + 1)
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, f"PDF przekracza {MAX_PDF_BYTES // (1024*1024)} MB.")
    if len(data) < 1024:
        raise HTTPException(400, "PDF wygląda na pusty.")

    # Count pages before pricing
    tmp_path = WORK_DIR / f"{uuid.uuid4().hex[:12]}.pdf"
    tmp_path.write_bytes(data)
    try:
        pages = len(pypdf.PdfReader(str(tmp_path)).pages)
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(400, f"Nie można odczytać PDF: {e}")

    if pages > MAX_PAGES:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(413, f"PDF ma {pages} stron, limit to {MAX_PAGES}.")
    if pages < 1:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(400, "PDF nie zawiera stron.")

    # No pre-payment text-extraction check: scanned PDFs are routed to OCR
    # mode in core.run() and produce real cards. They cost more than the
    # text path, but that's accounted for at generation time, not at upload.

    # Card mode comes from the form radio. Default simple per operator's call:
    # Paulina's feedback was that the prior output was "aż za szczegółowe" for
    # kolokwium use. Detailed unlocks the full schema (+ przeciwwskazania
    # + działania niepożądane) for egzamin prep.
    card_mode = "detailed" if (mode or "").lower() == "detailed" else "simple"
    qp = (quality_pass or "").lower() in ("true", "1", "yes", "on")

    job = _new_job(pdf.filename or "skrypt.pdf", pages, quality_pass=qp)
    job.card_mode = card_mode
    final_pdf_path = WORK_DIR / f"{job.job_id}.pdf"
    tmp_path.replace(final_pdf_path)
    job.pdf_path = final_pdf_path

    if not stripe.api_key:
        # No Stripe configured — refuse to create paid job rather than silently free-passing
        raise HTTPException(503, "Płatności niedostępne. Skontaktuj się z administratorem.")

    # Create Stripe Checkout Session
    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            # Stripe rejects unactivated methods at session create. card is universally enabled;
            # enable blik/p24 in your Stripe dashboard, then add them here.
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "pln",
                    "product_data": {
                        "name": (
                            f"fiszkomat — {job.pdf_name} ({pages} s.)"
                            + (" + quality pass" if job.quality_pass else "")
                        ),
                        "description": (
                            "Anki .apkg wygenerowany z polskiego skryptu"
                            + (" z przeglądem Claude 4.7" if job.quality_pass else "")
                        ),
                    },
                    "unit_amount": job.price_grosze,
                },
                "quantity": 1,
            }],
            client_reference_id=job.job_id,
            metadata={"job_id": job.job_id, "pages": str(pages)},
            success_url=f"{PUBLIC_BASE_URL}/pay/return?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{PUBLIC_BASE_URL}/pay/cancel?job_id={job.job_id}",
            expires_at=int(time.time() + 30 * 60),  # 30 min checkout window
        )
    except stripe.error.StripeError as e:
        # Don't leave the PDF on disk if checkout creation failed
        if job.pdf_path and job.pdf_path.exists():
            job.pdf_path.unlink()
        raise HTTPException(502, f"Stripe error: {e.user_message or str(e)}")

    job.stripe_session_id = session.id
    return JSONResponse({
        "job_id": job.job_id,
        "pages": pages,
        "price_pln": job.price_grosze / 100,
        "checkout_url": session.url,
    })


@app.get("/pay/return")
def pay_return(session_id: str) -> RedirectResponse:
    """Stripe redirects here after successful Checkout."""
    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.StripeError as e:
        raise HTTPException(502, f"Nie udało się zweryfikować płatności: {e.user_message or str(e)}")
    job_id = session.metadata.get("job_id") if session.metadata else None
    if not job_id:
        raise HTTPException(400, "Sesja płatności bez job_id.")
    job = _get_job(job_id)
    if session.payment_status == "paid":
        job.paid = True
        _kick_off_generation(job)
    return RedirectResponse(f"/?job={job_id}", status_code=303)


@app.get("/pay/cancel")
def pay_cancel(job_id: str) -> RedirectResponse:
    job = _get_job(job_id)
    job.status = "cancelled"
    if job.pdf_path and job.pdf_path.exists():
        try:
            job.pdf_path.unlink()
        except OSError:
            pass
        job.pdf_path = None
    return RedirectResponse("/", status_code=303)


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request) -> JSONResponse:
    """Backup queueing path in case the user closes the browser tab before /pay/return fires.
    Requires STRIPE_WEBHOOK_SECRET to be configured — without it we refuse rather than trust headers."""
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(503, "Webhook not configured.")
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        raise HTTPException(400, f"Invalid signature: {e}")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        job_id = (session.get("metadata") or {}).get("job_id")
        if job_id:
            try:
                job = _get_job(job_id)
            except HTTPException:
                return JSONResponse({"ignored": "job not found"})
            if session.get("payment_status") == "paid":
                job.paid = True
                _kick_off_generation(job)
    return JSONResponse({"received": True})


_COST_PATTERN = re.compile(
    r"(?:[,\s])?\$\d+\.\d+|in=\d+|out=\d+|cache_r=\d+|cache_w=\d+",
    re.IGNORECASE,
)


def _sanitize_log_lines(lines: list[str]) -> list[str]:
    """Strip USD costs + raw token counts from log lines before they reach
    the client. Operator-side full log is still preserved on `job.log_lines`;
    this only sanitizes the OUTGOING projection. Customers see card counts,
    chunk progress, and timing — not the wholesale API cost per chunk."""
    cleaned: list[str] = []
    for ln in lines:
        s = _COST_PATTERN.sub("", ln).rstrip(" ,")
        # Collapse repeated spaces left by the substitution
        s = re.sub(r" {2,}", " ", s)
        cleaned.append(s)
    return cleaned


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> JSONResponse:
    job = _get_job(job_id)
    return JSONResponse({
        "job_id": job.job_id,
        "pdf_name": job.pdf_name,
        "pdf_pages": job.pdf_pages,
        "status": job.status,
        "paid": job.paid,
        "free": job.free,
        "log_lines": _sanitize_log_lines(job.log_lines[-50:]),
        "stats": _stats_dict(job.stats),
        "error": job.error,
    })


@app.get("/jobs/{job_id}/deck")
def download_deck(job_id: str) -> FileResponse:
    job = _get_job(job_id)
    if job.status != "done" or not job.deck_path or not job.deck_path.exists():
        raise HTTPException(409, "Talia jeszcze niegotowa.")
    stem = Path(job.pdf_name).stem or "fiszkomat"
    return FileResponse(
        path=str(job.deck_path),
        media_type="application/octet-stream",
        filename=f"{stem}.apkg",
    )


@app.get("/jobs/{job_id}/cards")
def get_cards(job_id: str) -> FileResponse:
    job = _get_job(job_id)
    if job.status != "done" or not job.cards_path or not job.cards_path.exists():
        raise HTTPException(409, "Karty jeszcze niegotowe.")
    return FileResponse(path=str(job.cards_path), media_type="application/json")


@app.get("/study/{job_id}", response_class=HTMLResponse)
def study(job_id: str) -> str:
    # Sample slugs (zaj13 / zaj17 / …) collide with the {job_id} param when
    # someone hits /study/sample/zaj13 — FastAPI routes /study/sample to this
    # handler with job_id="sample". The fixed-string check below disambiguates;
    # the dedicated /study/sample/{slug} route is right below.
    if job_id == "sample":
        raise HTTPException(404, "Brak takiego zadania.")
    job = _get_job(job_id)
    if job.status != "done":
        # Job not finished — bounce back to index, which resumes polling via ?job=<id>.
        return f'<meta http-equiv="refresh" content="0;url=/?job={job_id}">'
    return STUDY_HTML


def _valid_sample_slug(slug: str) -> bool:
    return any(d["slug"] == slug for d in SAMPLE_DECKS)


@app.get("/study/sample/{slug}", response_class=HTMLResponse)
def study_sample(slug: str) -> str:
    """Sample-deck study mode. Inlines the cards as `window.__INLINE_CARDS`
    and the slug as `window.__SAMPLE_SLUG` so STUDY_HTML's JS uses them
    instead of fetching /jobs/<id>/cards."""
    if not _valid_sample_slug(slug):
        raise HTTPException(404, f"Nie ma takiej talii: {slug}")
    cards = _load_sample_deck(slug)
    if not cards:
        raise HTTPException(404, f"Talia {slug} jest pusta.")
    meta = next(d for d in SAMPLE_DECKS if d["slug"] == slug)
    title = meta["title"]
    # Inject the cards + slug just before STUDY_HTML's main <script>.
    inline = (
        '<script>'
        f'window.__SAMPLE_SLUG={json.dumps(slug)};'
        f'window.__SAMPLE_TITLE={json.dumps(title)};'
        f'window.__INLINE_CARDS={json.dumps(cards, ensure_ascii=False)};'
        '</script>'
    )
    return STUDY_HTML.replace("<script>\n(function(){", inline + "\n<script>\n(function(){", 1)


@app.get("/sample/{slug}/cards")
def sample_cards(slug: str) -> FileResponse:
    if not _valid_sample_slug(slug):
        raise HTTPException(404, f"Nie ma takiej talii: {slug}")
    path = _samples_dir() / f"{slug}.cards.json"
    if not path.is_file():
        raise HTTPException(404, "Karty nie istnieją na dysku.")
    return FileResponse(path=str(path), media_type="application/json")


@app.get("/sample/{slug}/deck")
def sample_deck(slug: str) -> FileResponse:
    if not _valid_sample_slug(slug):
        raise HTTPException(404, f"Nie ma takiej talii: {slug}")
    path = _samples_dir() / f"{slug}.apkg"
    if not path.is_file():
        raise HTTPException(404, "Plik .apkg nie istnieje na dysku.")
    return FileResponse(
        path=str(path),
        media_type="application/octet-stream",
        filename=f"fiszkomat-{slug}.apkg",
    )


def _stats_dict(s: RunStats | None) -> dict | None:
    """Client-facing stats only. Drops api_cost_usd + token counts —
    operator's wholesale API cost is internal and never shown to the
    customer. Full RunStats remains available server-side on the Job."""
    if s is None:
        return None
    return {
        "pdf_pages": s.pdf_pages,
        "chunks": s.chunks,
        "cards_valid": s.cards_valid,
        "cards_rejected": s.cards_rejected,
        "wall_seconds": s.wall_seconds,
    }


_recover_jobs_from_disk()  # module-load time; covers `uvicorn fiszkomat.web:app` invocation too


def serve() -> None:
    import uvicorn
    host = os.environ.get("FISZKOMAT_HOST", "127.0.0.1")
    port = int(os.environ.get("FISZKOMAT_PORT", "8000"))
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    uvicorn.run("fiszkomat.web:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    serve()
