# fiszkomat — SPEC v0.4

**Status:** Phase 1 **LIVE in production** at `fiszkomat.ewwesolutions.work`
(Cloudflare named tunnel → `localhost:8001`). Stripe live with PL-tier
charges enabled; 7 sample decks (296 cards) on the landing as the
warm-up funnel. Operator's dogfood user is the active test user.

**Owner:** solo operator
**Date:** 2026-05-14 (v0.4 — same day as v0.3, after a full-day pricing,
copy, agent-driven-content and UX sweep)
**Goal of this doc:** enough detail to resume tomorrow without re-reading
git log; covers what changed today + the resulting surface area.

> **v0.2 deltas vs v0.1:**
> - Phase 0 CLI shipped (`src/fiszkomat/`). 159 cards on `Leki-lato-…`
>   skrypt, 100% dogfood user acceptance, $0.18 API cost, 4 min wall.
> - Phase 1 web shell shipped locally (`src/fiszkomat/web.py`). Same
>   pipeline, exposed as `POST /jobs` → poll → `GET /jobs/{id}/deck`.
>   Polish UI matching the deck's aesthetic. Runnable: `python -m fiszkomat.web`.
> - Chunked-attention thesis empirically supported. PDF chunking is now
>   `Zajęcia N.` boundary-aware (see `core.py:detect_zajecia`).
> - Card-mode question (basic/cloze/both) resolved by ground-truth file
>   `fiszki-farmakologia.html`: pharma-group cards with `z,t,d,m,i,c`.
> - Truncation handling: `max_tokens` raised to 8192 + per-object salvage
>   regex for partial responses (`_salvage_card_objects`).
> - Outstanding: prompt cache doesn't engage (system prompt under 2048-tok
>   threshold); Phase 1 deployment + Stripe.
>
> **v0.3 deltas (2026-05-13):**
> - Prompt cache engaged: system prompt padded to ~4200 tokens with 5 more
>   ground-truth exemplars + extended style guide. Verified on Haiku 4.5
>   that chunk N+1 reads from cache.
> - V1.5 in-browser reviewer shipped: `GET /study/{job_id}` serves a
>   self-contained HTML page with a 4-button SM-style review loop (fixed
>   intervals tuned for kolokwium prep — 10min / 4h / 1d / 3d), inline
>   card editing (essential per operator: small generation errors get
>   fixed by the user, not re-generated), localStorage for review + edit
>   state, Polish UI, mobile-first responsive. No accounts, no backend
>   state for reviews.
> - `core.run()` now also writes `<id>.cards.json` alongside `.apkg` so
>   the reviewer can fetch the card data. `.apkg` remains the secondary
>   CTA for Anki diehards.
> - `FISZKOMAT_DEV_MAX_CHUNKS` env var: optional dev-mode cap on chunks
>   per generation, used for fast smoke tests (no production effect).
>
> **v0.4 deltas (2026-05-14) — the long day:**
>
> *Production*:
> - Public URL live: `https://fiszkomat.ewwesolutions.work` via Cloudflare
>   named tunnel (config in `~/.cloudflared/config.yml` on the operator's
>   Windows box; `ingress: fiszkomat.ewwesolutions.work → http://localhost:8001`).
> - `FISZKOMAT_PORT` env defaults to 8000, set to 8001 in production to
>   avoid collision with Faktomat on the same host.
> - Free-token bypass (`?t=PITONCZYK`) **removed** — was an abuse vector
>   once the site was publicly tunnelled. Operator + dogfood user now buy normally
>   or use sample decks.
>
> *Sample decks (the warm-up funnel)*:
> - Landing has a `FISZKI PRZYKŁADOWE` section with **7 curated decks**
>   covering most of the WMS farmakologia year:
>   Z8 Układ oddechowy (23), Z13 Toksykologia (18), Z15 Antybiotyki+antyseptyki
>   (53), Z16 Przeciwwirusowe+przeciwgrzybicze+pasożytnicze (37),
>   Z17 Leki układu pokarmowego (92), Z18 Hormony rozdz. 16 (38),
>   Z19 Metabolizm Ca + cukrzyca + otyłość (40). **296 cards total.**
> - Each deck has its own `/study/sample/<slug>` route reusing the
>   existing SM-style reviewer; cards inlined as `window.__INLINE_CARDS`
>   (avoids the /jobs/{id}/cards fetch). `/sample/<slug>/deck` serves
>   the `.apkg` directly.
> - Generation pipeline: **subagent reads PDF**. Operator's PDFs had
>   broken text layers (pypdf returned `\x01` control bytes). Workaround:
>   `pdftoppm` renders PDF → PNG (150 DPI), then a general-purpose
>   subagent reads every PNG with the Read tool and writes cards JSON
>   1:1 with skrypt content. Zero `ANTHROPIC_API_KEY` spend — uses
>   Claude Code MAX plan. Token cost across 4 PDFs (Z15/Z16/Z18/Z19):
>   ~249k MAX tokens, $0 API budget.
> - Sample decks shipped as committed assets:
>   `fiszkomat/test_docs/out/zaj<NN>.cards.json` + `.apkg`. Whitelisted
>   per-file in `.gitignore`. `SAMPLE_DECKS` in `web.py` is the source
>   of truth for landing tabs.
>
> *Picker UX*:
> - Old tabbed picker broke at 7 decks (tabs overflowed at >4). Replaced
>   with a **toggle between two views**:
>   - **Siatka** (default): CSS grid, 1 col mobile (<520px), 2 col tablet+.
>     Each tile = ZAJĘCIA + title + subtitle + count + STUDIUJ button +
>     `.apkg` link.
>   - **Lista**: compact rows, full-row clickable, count + arrow on the right.
>   Toggle state persists in `localStorage["fiszkomat-samples-view"]`.
> - `fiszkomat` wordmark wrapped in `<a href="/">` on landing + study
>   pages. Returns to home from anywhere.
>
> *Generate flow — quality pass*:
> - Click **Wygeneruj fiszki** → modal with **two variants**:
>   - **Standardowy** (Haiku 4.5 only).
>   - **Standardowy + quality pass** (Haiku → Opus 4.7 review).
>     Premium card has a "polecane" badge.
> - Modal: paper-cream content + rgba blur backdrop, click-outside / Esc
>   cancels. Hidden input `quality_pass` is set on variant pick, form
>   POST follows.
> - Backend `core.run()` accepts `quality_pass: bool`. After Haiku pass +
>   validation, if `quality_pass=True` the deck is sent to a single
>   `claude-opus-4-7` call with a "Polish pharmacology professor"
>   reviewer prompt — catches wrong drug groupings, mechanism errors,
>   indication mismatches, duplicates. Cards without errors are left
>   unchanged. Schema-validated again post-review; falls back to Haiku
>   output if parse fails (customer never gets nothing).
>
> *Pricing*:
> - Standard tier reduced 5/10/20/35 → **3/5/10/15 PLN**.
> - Quality-pass tier is a SEPARATE table (not a flat multiplier),
>   sized to never-loss against worst-case Opus 4.7 + Haiku-vision
>   wholesale: **5/8/16/25 PLN**. Worst-case margins 25-40% across all
>   tiers; standard tier still has 80%+ margin (Haiku floor is cheap).
> - The multiplier is not displayed in copy anywhere — customer sees
>   two prices, picks one.
>
> *PDF handling*:
> - `_printable_chars()` helper in `core.py` — counts only chars that
>   are `isprintable()` or whitespace. Used by `extract_pages` instead
>   of `len(t)`. Catches the broken-text-layer case (PDFs that return
>   `\x01` bytes pass a naive byte count but yield 0 cards from Haiku);
>   now correctly raises `EmptyPdfError` → vision-mode auto-engage.
> - Landing has a `/// obsługujemy` block listing the three PDF types
>   we accept: native (text layer), scanned (auto OCR via Claude vision),
>   broken-text-layer (detected → OCR).
> - Vision-mode Haiku is **NOT 3× more expensive** in practice (old
>   comment in `PRICE_TIERS` was from Haiku 3 days). Real data: vision
>   on 16p Z8 = $0.052, text on 13p Z13 = $0.047. Same ballpark.
>
> *User-facing log sanitization*:
> - `_sanitize_log_lines()` strips `$X.XXXX`, `in=N`, `out=N`,
>   `cache_r=N`, `cache_w=N` from log lines before they leave the
>   server. Operator's full log preserved at `job.log_lines`.
> - `_stats_dict()` drops `api_cost_usd`, token counts. Customer sees
>   card count + wall seconds only.
>
> *Polish copy fixes*:
> - "Wsadź → wrzuć", "Polski-pierwszy → polska specyfika", etc. in
>   landing — visual rework pass.
>   The deeper Faktomat copy rewrite landed in invoice_idp (PR #1).
> - Contact footer on the landing — contact email surfaced for
>   "talia from a different zajęcia" requests.

---

## 1. One-line pitch

A Polish-medical-specific flashcard generator that takes a long skrypt
PDF and produces an Anki deck — without the attention-degradation
failure mode that Gemini hits past ~10 pages.

## 2. Why this exists

- **Demand signal:** Polish med students use Gemini for flashcards and
  hit attention-degradation past ~10 pages — *"głupieje powyżej 10 stron,
  robi dobrze do połowy, potem gówno bez połowy rzeczy"*. A dogfood user
  validated this failure mode on a specific skrypt PDF in Phase 0.
- **Market shape:** ~50k Polish med students; every kolokwium and every
  sesja regenerates the demand. Currently they generate flashcards
  manually in paid tools (Quizlet PL, Brainscape PL) or fight Gemini.
- **Architectural moat:** chunked generation (4–5 page chunks, fresh
  attention per chunk) is the technical reason this doesn't degrade
  where one-shot LLM calls do.

## 3. Customer

**Phase 0 user — N=1:** a single Polish med-student dogfood user with a
specific skrypt PDF and a kolokwium in days. Validation = uses it instead
of Gemini and ≥70% of cards survive a cull pass.

**Phase 1 ICP — Polish med students:**
- Years 1–6 medical studies (Polish-language)
- Buys when blocked by a kolokwium/sesja, not on subscription whim
- Reachable via: Phase 0 user's peer group → WMS Facebook groups →
  year-level group chats → eventually paid Google Ads on Polish-medical
  keywords once unit economics confirmed

**Out of scope for now:** non-Polish, non-medical, or non-PDF inputs.
Out-of-scope expansion is a deliberate Phase 2+ decision.

## 4. The product loop (Phase 0 — CLI)

```
fiszkomat input.pdf [--out deck.apkg] [--mode basic|cloze|both]
                    [--chunk-pages 5] [--max-cards-per-chunk 25]
                    [--lang pl-med] [--dry-run]
```

1. **Ingest** — read PDF, extract per-page text. Preserve diacritics
   (UTF-8). Detect headers, lists, definitions, anatomical names,
   pharmacology dosages, mechanism descriptions.
2. **Chunk** — slice into 4–5 page chunks (configurable). Each chunk
   carries forward a *running glossary* of named entities seen in earlier
   chunks so the model can reference (but not duplicate) cards across
   chunk boundaries.
3. **Generate** — one Anthropic call per chunk. Sonnet by default.
   Prompt-cache the chunking instructions + Polish-medical guidance +
   Anki formatting rules. Output structured JSON (one schema per card
   mode).
4. **Validate** — every card must:
   - Have Polish on both sides (no accidental English leakage)
   - Pass a length budget (front ≤ 200 chars, back ≤ 600 chars)
   - Not duplicate a card already produced in this run (Levenshtein on
     front side)
   - Reference at least one term from the source chunk (substring or
     stem match)
5. **Pack** — emit an Anki `.apkg` via `genanki`-equivalent. Deck name
   = PDF basename. Card model = chosen mode (basic / cloze / both).
   Tags = `fiszkomat::<pdf-basename>::chunk-<n>`.
6. **Report** — print summary: `N pages → M chunks → K cards (J basic,
   L cloze), $X API spend, T seconds`.

## 5. Card schema (updated after seeing dogfood user's ground truth)

`fiszki-farmakologia.html` ships with 158 hand-made ground-truth cards
across zajęcia 1–10 in a clean JS array. Schema observed:

```
{ z: <int>,        // zajęcia number
  t: <str>,        // group title (e.g. "Antagoniści receptorów muskarynowych")
  d: <str>,        // drugs (comma-separated list)
  m: <str>,        // mechanizm — one or two sentences
  i: <str>,        // wskazania
  c: <str>         // przeciwwskazania
}
```

This is **the** card shape for farmakologia content. Phase 0 generator
emits exactly this schema, packs it into Anki via `genanki` with five
visible fields (Tytuł, Leki, Mechanizm, Wskazania, Przeciwwskazania) +
a hidden `Zajęcia` tag. Basic/cloze decision is deferred — for this
skrypt the structure is given. For non-pharma skrypty (future), we'll
detect and switch modes; that is out of scope for Phase 0.

The ground-truth file is also used as **(a)** few-shot examples in the
generator prompt (~5 representative cards) and **(b)** a held-out
evaluation set (the remaining ~150 cards used to score Phase 0 output
quality without leaking through the prompt).

## 6. Phase 0 — CLI deliverable ✅ SHIPPED 2026-05-13

**Done when:**
- [x] Operator can run `fiszkomat <skrypt>.pdf` on the dogfood user's machine
      (Windows; offline-ish — only Anthropic API call required) ✅ (`pip install -e .`)
- [x] Output is an `.apkg` file Anki Desktop opens without error ✅
      (`Leki-lato.apkg` opens cleanly)
- [x] Cost per 200p PDF measured and logged ✅
      ($0.18 for 38p — projects to ~$1.00 for 200p; measured without prompt caching)
- [x] dogfood user uses it on her real kolokwium skrypt ✅
- [x] ≥70% of cards survive her cull pass ✅ — operator quote:
      *"Anki cards are spot on 100% correct amazing I love you keep going"*

**Phase 0 → Phase 1 gate measurements (all green):**

| Gate criterion | Threshold | Actual |
|---|---|---|
| dogfood user accepts deck for real kolokwium revision | yes | yes |
| Cards surviving cull pass | ≥ 70% | ~100% |
| Cost per skrypt | ≤ $2.00 | $0.18 (38p) |
| Generation time | ≤ 5 min | 3 min 53s |
| Diacritics + Polish-only on sample | 100% | 100% |

**Out of scope for Phase 0:**
- Web UI
- Stripe / billing / accounts
- Anki Cloud sync (deck file is enough; Anki Desktop handles sync)
- Image extraction from PDF (text-only Phase 0)
- Image regeneration / labeling
- LaTeX / formulas (note as a known limitation)
- Polish→English translation (Polish-only end-to-end)

## 7. Phase 1 — web app 🟡 LOCAL SHELL SHIPPED, REMAINDER PENDING

**Shipped:**
- FastAPI app in `src/fiszkomat/web.py`. Runnable: `python -m fiszkomat.web`.
- `GET /` — Polish-language upload form, vanilla HTML, styling matched to
  the deck aesthetic (cream paper, deep red titles, JetBrains Mono labels).
- `POST /jobs` — multipart PDF upload (30 MB cap, content-type check,
  empty-file check). Writes PDF to `_work/<id>.pdf`, kicks off generation
  in a background executor.
- `GET /jobs/{id}` — JSON status: queued / running / done / failed,
  log_lines, stats, error.
- `GET /jobs/{id}/deck` — file download of the `.apkg`.
- Privacy: source PDF is deleted from disk as soon as generation finishes
  (success or failure). Decks expire after 1 hour.
- In-memory job registry (single-instance — OK for early Phase 1; revisit
  on first concurrent-user complaint).
- HTTP smoke test (`POST /jobs` → poll → `GET /jobs/{id}/deck`) passed
  end-to-end on 2026-05-13 with the same 38p skrypt; deck downloaded
  identical to CLI output (158 cards, 1 dedup, $0.18, 233s).

**Shipped (2026-05-13, same day):**
- Stripe Checkout gating wired and smoke-tested. `POST /jobs` returns
  either `{job_id, checkout_url}` (default) or `{job_id, free: true}`
  when the request includes the operator's free token. Pricing: **5 PLN
  for ≤200p, 10 PLN for 201–500p**. Cap at 500p enforced server-side.
  Card-only payment (Stripe rejected p24/blik on operator's account —
  enable in dashboard later if wanted).
- `GET /pay/return` verifies the session and kicks off generation.
- `POST /stripe/webhook` is a backup queueing path; refuses with 503
  unless `STRIPE_WEBHOOK_SECRET` is configured.
- `FISZKOMAT_FREE_TOKEN` env var = bookmark URL bypass (`?t=<token>`)
  for operator + dogfood user use without paying themselves.
- PDF page count happens before the checkout session is created, so
  pricing is per-doc-accurate.

**Outstanding:**

- Deployment to AWS free-tier EC2 + Caddy + a domain. Local-only runs
  fine for dogfood user on operator's machine; deployment is needed once the WMS
  Facebook seeding starts.
- Prompt-cache fix: the system prompt is below Haiku's 2048-token cache
  threshold so `cache_read=cache_write=0` on every run. Pad the system
  prompt (more exemplars, or pre-loaded reference glossary) or switch to
  the multi-message cache pattern. Expected impact: ~10× reduction in
  input-token cost on repeat runs.
- Concurrency: in-memory job registry won't survive a restart; use SQLite
  or move to a worker queue (Redis + RQ) once >1 user is realistic.
- Email-link fallback for the deck download (currently direct download
  only — fine for the single-session flow).

**Phase 1 distribution:**
1. dogfood user posts in WMS Facebook groups (her natural seeding)
2. Operator-side: programmatic SEO pages for the most common skrypt
   names by year + specialty (one page each, agent-generated)
3. Once Phase 0 validates: paid Google Ads on Polish-medical keywords,
   capped at $30/day until conversion confirmed

## 8. Tech stack

### Phase 0 (CLI)

- **Language:** Python 3.11+ (matches `invoice_idp`, `momentum`,
  `crypto_momentum` — easiest cross-project tooling)
- **PDF:** `pypdf` for text extraction; `pdfplumber` fallback for
  structured pages
- **Anthropic SDK:** `anthropic` Python package, Sonnet by default
  (claude-sonnet-4-6), prompt caching on the chunking instructions
- **Anki:** `genanki` for `.apkg` emission
- **Schema:** `pydantic` for card validation
- **CLI:** `typer` or `argparse` (start with stdlib `argparse`)
- **Config:** `.env` for `ANTHROPIC_API_KEY` only; no other secrets in
  Phase 0

### Phase 1 (web)

- Backend: FastAPI + Uvicorn (matches `invoice_idp`)
- Frontend: minimal Polish-language single-page; HTMX or vanilla; no
  framework heaviness in Phase 1
- Storage: ephemeral — `/tmp` style, with a hard TTL
- Billing: Stripe Checkout (one-time per doc)
- Hosting: AWS free-tier EC2 + Caddy (reuses operator's `invoice_idp`
  ops template)

## 9. Unit economics

**Phase 0 (cost only):**
- Sonnet input: ~$3 per million tokens (cache discount applies to the
  prompt-cached instructions)
- Sonnet output: ~$15 per million tokens
- Estimate for a 200p PDF: ~40 chunks × ~3k input + 2k output per chunk
  ≈ 120k input + 80k output ≈ $0.36 + $1.20 ≈ **~$1.50–$2.00 per
  200p doc** (Sonnet, before caching discount)
- With cache hit on chunking instructions: closer to **$1.00–$1.50**
- **Hard rule (from memory `project_fiszkomat.md`): if cost > 5 PLN
  retail price per doc, revisit pricing or chunking before launch.**
  At 5 PLN ≈ $1.25 retail, Phase 0 measurement decides the verdict.

**Phase 1 pricing:**
- 5 PLN per ≤200p skrypt → ~3× cost markup at the upper estimate
- 10 PLN per 201–500p skrypt → ~2× cost markup
- Reverse if Phase 0 measurement comes in lower than $1.50

**Break-even (against ~770 PLN/mo operating burn):**
- 770 / 5 = **154 paid docs/mo**
- 154 / 30 ≈ 5 paid docs/day — plausible if organic seeding works and a few
  WMS year groups adopt around exam windows

## 10. 48-hour Phase 0 build plan

| Hour block | Deliverable |
|---|---|
| 0–2 | Project scaffold, `pyproject.toml`, `.env.example`, CLI skeleton with `--dry-run` that prints chunk plan only |
| 2–6 | PDF ingest + chunking; smoke test on the dogfood user's actual skrypt; verify text quality, diacritics, page boundary detection |
| 6–10 | Anthropic call per chunk; structured JSON output; basic card validation |
| 10–14 | `genanki` packing → `.apkg`; manual open in Anki Desktop on operator's machine |
| 14–18 | Validation pass: dedup, Polish-only enforcement, length budgets, glossary chaining across chunks |
| 18–24 | Real run on dogfood user's full skrypt; cost measurement; dogfood user cull pass; ≥70% survive gate check |
| 24–36 | Iteration on prompt + chunking based on cull pass feedback |
| 36–48 | Documentation: README usage, `HANDOFF.md` for resumability |

**Green-light to Phase 1:** dogfood user uses fiszkomat-generated deck for the
actual kolokwium revision (not a parallel comparison with Gemini —
real use). If she switches, Phase 1 starts.

**Red-light:** if cull pass <70% after 3 prompt iterations, hold and
investigate whether chunked-attention is actually the failure mode, or
whether the failure mode is something else (e.g. medical jargon
fluency, not attention).

## 11. Phase 0 → Phase 1 gate

| Gate criterion | Threshold |
|---|---|
| dogfood user uses CLI output for real kolokwium revision | yes |
| Cards surviving dogfood user cull pass | ≥ 70% |
| Cost per 200p PDF | ≤ $2.00 (= ~8 PLN, leaves room to price at 5 PLN with thin margin OR re-price upward) |
| Generation time per 200p PDF | ≤ 5 minutes wall-clock |
| Diacritics + Polish-only output | 100% on a 50-card sample |

If 4/5 pass → green-light Phase 1. If <4/5 → iterate Phase 0 first.

## 12. Risks & kill conditions

1. **Attention is not actually the failure mode.** Maybe Gemini fails
   on Polish medical terminology fluency, not on attention degradation.
   In that case, chunking won't help and Sonnet must be the moat alone.
   - **Detect:** Phase 0 measurement, dogfood user cull pass on a chunk that's
     entirely in the latter half of the skrypt.
   - **Mitigation:** Sonnet's Polish-medical performance is empirically
     measured during Phase 0; if it's the moat, pricing/messaging
     adjusts.

2. **Cost overruns past 5 PLN/doc retail breakeven.**
   - **Detect:** Phase 0 cost telemetry.
   - **Mitigation:** larger chunk size (8–10 pages), tighter card count
     per chunk, Haiku for first-pass and Sonnet only for difficult
     chunks, or upward repricing if quality justifies.

3. **dogfood user doesn't use it.** Tool ships, she stays on Gemini out of habit.
   - **Detect:** Phase 0 day-3 check-in.
   - **Mitigation:** instrument what's missing (UI? speed? card quality?)
     and address. If the answer is "Gemini is just good enough" then
     market signal is weaker than memory suggests and the spec needs a
     reality check.

4. **Polish-medical-specific terminology drift in cards.** Cards include
   technical errors that survive validation but the dogfood user spots them.
   - **Detect:** dogfood user cull pass with reason codes.
   - **Mitigation:** few-shot examples of correct medical card phrasing
     in the prompt; explicit "do not invent dosages or mechanisms"
     instruction.

5. **Anki ecosystem incompatibility.** `.apkg` opens but cards look
   broken (LaTeX, special chars, line breaks).
   - **Detect:** manual open in Anki Desktop in Phase 0 hour 10–14.
   - **Mitigation:** strict ASCII subset for special chars; document
     LaTeX as known-unsupported in Phase 0.

## 13. Definition of success (90-day)

- Phase 0 gate passed (dogfood user uses it; ≥70% cards survive cull)
- Phase 1 deployed
- ≥ 20 paid runs by end of June (= sesja peak)
- ≥ 100 paid runs by end of August (= start of new academic year prep)
- Cost per doc ≤ $1.50 in production (with caching working)
- Refund rate ≤ 10%
- One organic referral chain visible (paying user heard about it from a
  non-dogfood user source)

If hit: expand to non-medical Polish skrypty (law, engineering),
non-Polish (English-medical), and a subscription tier for med students
who run >5 docs/mo at exam time.

If missed: the failure mode tells us where to pivot — pricing? card
quality? distribution? Polish-medical specificity? Each has a different
fix and the Phase 0 telemetry pinpoints which.
