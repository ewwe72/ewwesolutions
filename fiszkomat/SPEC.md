# fiszkomat — SPEC

**Status:** 🟢 Phase 1 **LIVE in production** at
[fiszkomat.ewwesolutions.work](https://fiszkomat.ewwesolutions.work)
(Cloudflare named tunnel inside the Hyper-V VM → `localhost:8001`).
Stripe live with PL-tier charges; **7 sample decks (296 cards)** on the
landing as the warm-up funnel. Operator's GF is the active test user.

**Owner:** solo operator
**Last substantive update:** 2026-05-14 evening (v0.5 reviewer UX pass)

**See also:**
- [`CHANGELOG.md`](CHANGELOG.md) — chronological build log, version
  deltas (v0.1 → v0.5 and forward)
- [`README.md`](README.md) — user-facing project overview
- [`../CLAUDE.md`](../CLAUDE.md) — playspace routing + "Profit
  prioritisation" (why fiszkomat is near-term primary)
- [`../docs/vm-migration-2026-05-14.md`](../docs/vm-migration-2026-05-14.md)
  — runbook for the systemd-on-VM deployment

**Goal of this doc:** describe what fiszkomat IS today and the
forward-looking decisions (distribution, success criteria, risks). For
how we got here, read the CHANGELOG.

---

## 1. One-line pitch

A Polish-medical-specific flashcard generator that takes a long skrypt
PDF and produces an Anki deck — without the attention-degradation
failure mode that Gemini hits past ~10 pages.

## 2. Why this exists

- **Demand signal:** operator's GF is a Polish med student; uses Gemini
  for flashcards; complains it *"głupieje powyżej 10 stron, robi dobrze
  do połowy, potem gówno bez połowy rzeczy"* — attention degrades on
  long docs. She is the dogfood user.
- **Market shape:** ~50k Polish med students; every kolokwium and every
  sesja regenerates the demand. Currently they generate flashcards
  manually in paid tools (Quizlet PL, Brainscape PL) or fight Gemini.
- **Architectural moat:** chunked generation (4–5 page chunks,
  `Zajęcia N.` boundary-aware, fresh attention per chunk) is the
  technical reason this doesn't degrade where one-shot LLM calls do.
- **Distribution moat:** GF organically seeds her year + WMS Facebook
  groups. This is the one community vector that doesn't require
  operator socialising.

## 3. Customer

**Dogfood (N=1, ongoing):** operator's GF. Med student using fiszkomat
for kolokwium prep, feedback positive as of 2026-05-14 ("it's going
great"). Validation gate met: she uses it instead of Gemini, ≥70% of
cards survive her cull pass (actual: ~100%).

**ICP — Polish med students:**
- Years 1–6 medical studies (Polish-language)
- Buys when blocked by a kolokwium/sesja, not on subscription whim
- Reachable via: dogfood user's peer group → WMS Facebook groups →
  year-level group chats → eventually paid Google Ads on Polish-medical
  keywords once unit economics confirmed

**Out of scope for now:** non-Polish, non-medical, or non-PDF inputs.
Out-of-scope expansion is a deliberate Phase 2+ decision.

## 4. The product loop

Generation pipeline (shared between CLI and web surface):

1. **Ingest** — read PDF, extract per-page text (`pypdf` →
   `pdfplumber` fallback). Preserve diacritics (UTF-8). If the text
   layer is broken (`_printable_chars()` below threshold) or absent
   (scanned PDF), the pipeline falls back to **Claude vision mode**
   via `pdftoppm` rendering at 150 DPI.
2. **Chunk** — slice into 4–5 page chunks, boundary-aware on
   `Zajęcia N.` headers (`core.py:detect_zajecia`). Each chunk
   carries forward a *running glossary* of named entities seen
   earlier so the model can reference (but not duplicate) cards
   across chunk boundaries.
3. **Generate** — one Anthropic call per chunk. **Haiku 4.5** is the
   default (cheap, fast). System prompt is padded to ~4200 tokens
   with ground-truth exemplars + extended style guide so the prompt
   cache engages on chunk N+1.
4. **Validate** — every card must:
   - Have Polish on both sides (no accidental English leakage —
     current detector inspects only `m` field for ≥2 of five hardcoded
     English stopwords; see `docs/fiszkomat-prompt-audit.md` for
     limits)
   - Pass a length budget (caps: `t` ≤ 200, `m` ≤ 800, `i` ≤ 600,
     `c` ≤ 600, `n` ≤ 800 — no minima enforced)
   - Not duplicate a card already produced in this run (sha1 of
     `(t + "|" + d).lower()` — exact hash match, NOT fuzzy/Levenshtein;
     pinned by regression test in `tests/test_validation.py`)
   - Reference at least one term from the source chunk (substring
     or stem match)
5. **(Optional) Quality pass** — if the user picked the premium tier,
   the validated deck is sent to a single `claude-opus-4-7` call
   with a "Polish pharmacology professor" reviewer prompt. Catches
   wrong drug groupings, mechanism errors, indication mismatches,
   duplicates. Cards without errors are left unchanged. Schema-
   validated again post-review; falls back to Haiku output if parse
   fails (customer never gets nothing).
6. **Pack** — emit an Anki `.apkg` via `genanki`, plus a sibling
   `<id>.cards.json` consumed by the in-browser reviewer. Deck name =
   PDF basename. Five visible fields (Tytuł, Leki, Mechanizm,
   Wskazania, Przeciwwskazania) + hidden `Zajęcia` tag.
7. **Report** — sanitized log lines (no costs, no token counts) +
   stats (card count + wall seconds).

## 5. Card schema

`fiszki-farmakologia.html` (the ground-truth file from GF's existing
deck) ships with 158 hand-made cards across zajęcia 1–10. Schema:

```
{ z: <int>,        // zajęcia number
  t: <str>,        // group title (e.g. "Antagoniści receptorów muskarynowych")
  d: <str>,        // drugs (comma-separated list)
  m: <str>,        // mechanizm — one or two sentences
  i: <str>,        // wskazania
  c: <str>         // przeciwwskazania
}
```

This is **the** card shape for farmakologia content. The generator
emits exactly this schema. The ground-truth file is used as **(a)**
few-shot examples in the generator prompt (~5 representative cards)
and **(b)** a held-out evaluation set (the remaining ~150 cards used
to score output quality without leaking through the prompt).

For non-pharma skrypty (future Phase 2), mode detection + schema
switching is required; out of scope today.

## 6. What's shipped (Phase 0 + Phase 1)

**Phase 0 — CLI** ✅ SHIPPED 2026-05-13. Gate measurements all green
(see CHANGELOG). The CLI (`fiszkomat <skrypt>.pdf`) remains available
for headless one-off use.

**Phase 1 — web app** ✅ SHIPPED 2026-05-14. Live at
`fiszkomat.ewwesolutions.work`. End-to-end paying flow works:

- Landing with 7 sample decks (296 cards) as warm-up funnel — view
  toggle between Siatka (CSS grid) and Lista. Per-deck route
  `/study/sample/<slug>` reuses the SM-style reviewer with cards
  inlined.
- **`POST /jobs`** — multipart PDF upload (30 MB cap, content-type
  check). Page count → pricing tier → Stripe Checkout session →
  `GET /pay/return` verifies + kicks off generation. Backup
  `POST /stripe/webhook` queueing path (refuses with 503 unless
  `STRIPE_WEBHOOK_SECRET` configured).
- **Generation modal** — two variants on `Wygeneruj fiszki` click:
  Standardowy (Haiku only) or Standardowy + quality pass (Haiku →
  Opus 4.7 review, "polecane" badge).
- **`/study/{job_id}`** — self-contained HTML reviewer with 4-button
  SM-style review loop (10min / 4h / 1d / 3d), inline card editing,
  list view + per-card status, reset button (clears reviews, keeps
  edits), "opanowane" tooltip. All state in `localStorage`. Polish
  UI, mobile-first. No backend state for reviews — accounts-free.
- **`GET /jobs/{id}/deck`** — `.apkg` download for Anki diehards.
- **Privacy:** source PDF deleted as soon as generation finishes.
  Decks expire after 1h. In-memory job registry (single-instance —
  revisit on first concurrent-user complaint).
- **PDF handling robust to three input types:** native (text layer),
  scanned (OCR via Claude vision), broken-text-layer (detected via
  `_printable_chars()` heuristic, auto OCR fallback). Listed on the
  landing under `/// obsługujemy`.
- **Production runtime:** systemd unit `fiszkomat.service` on the
  Hyper-V Ubuntu VM (`~/playspace/random/fiszkomat/.venv/`).
  cloudflared inside the VM proxies the public domain to
  `localhost:8001`. `FISZKOMAT_PORT=8001` to avoid Faktomat
  collision. Deploy after edit: `sudo systemctl restart fiszkomat`.

## 7. What's pending

- **Concurrency / persistence.** In-memory job registry won't survive
  a restart (currently a `systemctl restart` mid-job loses it). Move
  to SQLite or Redis + RQ once a second simultaneous user is
  realistic — i.e. when WMS Facebook seeding lands.
- **Email-link fallback for deck download.** Currently direct
  download only; fine for single-session flow but breaks if user
  closes the tab before generation finishes.
- **Stripe payment methods.** Card-only today; Stripe rejected
  p24/BLIK on operator's account. Re-request in dashboard once volume
  justifies the support burden.
- **Programmatic SEO pages** for the most common skrypt names by year
  + specialty (one page each, agent-generated). Operator-side Phase 2
  distribution work, not blocking.
- **Paid Google Ads** on Polish-medical keywords, capped at $30/day
  until conversion confirmed. Phase 2 distribution.
- **Non-pharma skrypt support** (anatomia, fizjologia, biochemia,
  patomorfologia). Schema-detection + mode-switching work. Phase 2+.

## 8. Tech stack

- **Language:** Python 3.11+ (matches `invoice_idp`, `momentum`,
  `crypto_momentum` — easiest cross-project tooling)
- **Backend:** FastAPI + Uvicorn (matches `invoice_idp`)
- **PDF:** `pypdf` for text extraction; `pdfplumber` fallback;
  `pdftoppm` for the OCR/vision path
- **Anthropic SDK:** direct API. Haiku 4.5 default
  (`claude-haiku-4-5`); Opus 4.7 (`claude-opus-4-7`) for the
  quality-pass tier. System prompt padded to engage the cache.
- **Anki:** `genanki` for `.apkg` emission
- **Schema:** `pydantic` for card validation
- **Frontend:** vanilla HTML/CSS/JS, no framework. The in-browser
  reviewer is one Python string in `src/fiszkomat/study_html.py`
  (CSS + HTML + JS) — no build step. Cream paper / deep red /
  JetBrains Mono aesthetic.
- **Billing:** Stripe Checkout (one-time per doc)
- **Storage:** ephemeral `_work/<id>.pdf` + `.apkg` + `.cards.json`,
  hard TTL 1h. No DB.
- **Hosting:** Hyper-V Ubuntu 24.04 VM, systemd unit, cloudflared
  inside the VM. See `docs/vm-migration-2026-05-14.md`.
- **Config:** `.env` for `ANTHROPIC_API_KEY`, Stripe keys,
  `FISZKOMAT_PUBLIC_BASE_URL`. `FISZKOMAT_FREE_TOKEN` *removed* in
  v0.4 (was an abuse vector once public).

## 9. Unit economics + pricing

### Cost per generation (Haiku-default)

Measured real data (v0.4 telemetry):

- Vision-mode Haiku on 16p Z8 = $0.052
- Text-mode Haiku on 13p Z13 = $0.047

Vision and text are the same ballpark (~$0.003–0.004 per page).
A 200p skrypt → roughly $0.60–$0.80 in API cost with cache hits.

The quality-pass tier adds one `claude-opus-4-7` review call.
Worst-case bound is in CHANGELOG v0.4 §Pricing.

### Tiers

**Standardowy** (Haiku-only):

| Pages | Price |
|---|---:|
| ≤ 50 | 3 PLN |
| 51–150 | 5 PLN |
| 151–300 | 10 PLN |
| 301–500 | 15 PLN |

**Standardowy + quality pass** (Haiku → Opus 4.7):

| Pages | Price |
|---|---:|
| ≤ 50 | 5 PLN |
| 51–150 | 8 PLN |
| 151–300 | 16 PLN |
| 301–500 | 25 PLN |

Worst-case margins: 25–40% on quality-pass tier (Opus is the floor);
80%+ on standard tier (Haiku floor is cheap). The multiplier is **not
displayed** in copy — customer sees two prices, picks one.

### Break-even (vs 770 PLN/mo playspace burn)

- 770 / 5 PLN avg ≈ **154 paid docs/mo** = ~5/day
- Plausible if GF seeding works and a few WMS year groups adopt
  around exam windows

## 10. Risks & kill conditions

1. **Attention is not actually the failure mode.** Maybe Gemini fails
   on Polish medical terminology fluency, not on attention
   degradation. In that case, chunking won't help and the moat is
   prompt-tuning + Polish-medical-specific fine-shots.
   - **Detect:** GF cull pass on a chunk entirely in the latter half
     of the skrypt.
   - **Status:** v0.2 evidence supports the attention thesis (100%
     cull-pass on a 38p document) — provisional pass.

2. **Cost overruns past breakeven.** Today's margin is comfortable;
   risk is if a new model family makes "always-Opus" the expectation.
   - **Detect:** monthly cost-per-deck telemetry.
   - **Mitigation:** larger chunks, Haiku-only floor for paid tier,
     or upward repricing.

3. **GF stops using it.** Active dogfood is the truthtelling channel.
   - **Detect:** weekly check-in. As of 2026-05-14 she is still
     actively using it for kolokwium prep.
   - **Mitigation:** triage feedback (UI? speed? card quality?). The
     v0.5 reviewer UX pass came directly from her feedback.

4. **Polish-medical-specific terminology drift in cards.** Cards
   include technical errors that survive validation but a specialist
   spots them.
   - **Detect:** spot-check on quality-pass output; cull-pass rate.
   - **Mitigation:** few-shot examples of correct phrasing;
     explicit "do not invent dosages or mechanisms" instruction;
     quality-pass tier as the rigour-when-it-matters lever.

5. **Anki ecosystem incompatibility.** `.apkg` opens but cards look
   broken (LaTeX, special chars, line breaks). Currently the in-
   browser reviewer is the primary surface — `.apkg` is a secondary
   CTA for Anki diehards, so this is mitigated structurally.

## 11. Definition of success (90-day)

- ✅ Phase 0 gate passed (GF uses it; ≥70% cards survive cull)
- ✅ Phase 1 deployed
- ⬜ ≥ 20 paid runs by end of June (= sesja peak)
- ⬜ ≥ 100 paid runs by end of August (= start of new academic year prep)
- ⬜ Cost per doc ≤ $1.50 in production (caching working — currently met)
- ⬜ Refund rate ≤ 10%
- ⬜ One organic referral chain visible (paying user heard about it
  from a non-GF source)

**If hit:** expand to non-medical Polish skrypty (law, engineering),
non-Polish (English-medical), and a subscription tier for med
students who run >5 docs/mo at exam time.

**If missed:** the failure mode tells us where to pivot — pricing?
card quality? distribution? Polish-medical specificity? Each has a
different fix and the telemetry pinpoints which.

---

## 12. Cross-references

- [`../CLAUDE.md`](../CLAUDE.md) — playspace routing + "Profit
  prioritisation" (why fiszkomat is near-term primary)
- [`README.md`](README.md) — user-facing project overview
- [`CHANGELOG.md`](CHANGELOG.md) — chronological build log (v0.1 →
  current state)
