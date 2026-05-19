# Changelog

Chronological build log, newest first. SPEC.md describes current state;
this file is the trail of how we got there.

---

## 2026-05-17 — sample-deck quality pass round 2 (operator-requested, PDF-anchored)

Operator flagged "there are still some mistakes left" on the landing sample
decks. Prior pass (commit 8720462, 2026-05-15) fixed 8 errors found by a
narrow agent survey; this pass is exhaustive across all 7 decks.

Method: spawned 7 parallel general-purpose audit agents (one per deck),
each reading its source PDF in full plus its `.cards.json`, instructed to
report only PDF-citable factual errors with verbatim quotes. Applied only
fixes with clean PDF anchors; spot-verified key claims (atropine dose,
glukagon, nalokson, skleroterapia route, vitamin B index) by direct PDF
read. Skipped soft findings where the card adds clinically-correct content
the PDF simply doesn't cover (the "don't make things up" gate cuts both
ways — we don't introduce PDF-unsupported corrections either).

### What changed (one commit per deck, in commit order)

- `d3704cc` zaj13 (toksykologia, 4 fixes): atropina pediatric dose
  0,02 → 0,05 mg/kg; glukagon bolus 3–10 → 5–10 mg; fomepizol /
  hydroksykobalamina "lek pierwszego wyboru" framing softened
  (PDF marks both "niedostępny w Polsce").
- `6005a5b` zaj08 (układ oddechowy, 4 fixes): folkodyna moved from
  nieopioidowe to opioidowe per PDF taxonomy; ambroksol/bromheksyna
  removed from mukolityki (PDF puts them in 17.1.3 mukokinetyki);
  lewodropropizyna mechanism corrected; pirfenidon/nintedanib AE swap.
- `fd8728f` zaj19 (metabolizm wapnia, 7 fixes): "Paloperteryparatyd"
  → "Palopegteryparatyd" + Abaloparatyd added; "Afotaza" → "Asfotaza
  alfa"; fabricated "Sukrynogabag" → "Sukcynobukol" (with corrected
  mechanism + indication); klodronowy + tyludronowy removed from
  non-nitrogen card; bremelanotyd removed from obesity card; weekly
  PTH dosing corrected to 24-h stable levels; kalcytonina invented
  renal effect removed.
- `e609f4c` zaj18 (hormony przysadki, 4 fixes): protyrelina scope
  corrected to hypothyroid-only; "wydłużenie QT" removed from GnRH
  agonist AE list (it's antagonist AE); osilodrostat reclassified
  CYP11B2-primary; abirateron removed from HTZ drug list.
- `709cd52` zaj17 (układ pokarmowy, 4 fixes): skleroterapia route
  "podskórnie" → "podśluzówkowo" (dangerous teaching error); GABA-B
  mechanism in GERD corrected to TLESR; D2 antagonist mechanism
  corrected ("blokowanie receptorów" not "hamowanie uwalniania");
  vitamin B5 → B2 in hepatoprotekcja card.
- `ffb1062` zaj16 (przeciwwirusowe, 17 fixes): the heaviest deck.
  Includes hallucinated drug "Anuwimab" replaced with Imikwimod;
  fabricated kabotegrawir rectal route → IM injection;
  flucytozyna permease/deaminase mechanism swap fixed; nitroimidazole
  "fungobójczo" on T. vaginalis (a protozoan) → "pierwotniakobójczo";
  suramina CNS-stage claim flipped (does NOT cross BBB); 4 INN typos
  fixed (trotyzmu, dipropoksyl, niserwimab, Meklosprol).
- `ca874e0` zaj15 (antybiotyki, 8 fixes): "Chloroksyna" →
  "Chlorchinaldol"; hallucinated INN "Pruflinacyna" removed; etakrydyna
  removed from detergent card (it's a barwnik); telitromycyna removed
  from makrolidy (it's ketolid); streptomycyna removed from TB I rzutu
  (PDF puts it II rzutu); minocyklina + klarytromycyna removed from
  leprosy drugs.

### Verification

```
cd fiszkomat && .venv/bin/python -m pytest tests/   # 147 passed in 0.35s
python3 scripts/audit_secrets.py                    # [OK] no secrets
```

### Not regenerated this commit

The `.apkg` artefacts in `test_docs/out/*.apkg` are still from the prior
generation. Operator should regenerate after reviewing the JSON diffs
(see prior pattern in commit `ccd83f8`).

### Explicitly NOT touched (out of "PDF-anchored" scope)

- zaj13 cards 12 (Emulsja lipidowa) and 16 (Naltrekson) — entire LLM-
  generated cards covering drugs not in source PDF. Medically accurate
  but content-judgment to keep/remove (left as-is).
- zaj08 cards 11/12/13 (benralizumab IV-vs-SC; omalizumab w pokrzywce;
  dupilumab w eozynofilowym zapaleniu przełyku) — cards add real
  ChPL-current indications the PDF doesn't cover.
- zaj19 cards 0/8 (missing dokserkalcyferol/parykalcytol; "raz w roku"
  zoledronate) — incompleteness/real-world detail, not PDF-contradiction.

---

## 2026-05-17 — English-leakage detector broadened (audit recommendation #2)

Closes the remaining "real gap" the 2026-05-16 prompt-safety audit
surfaced in §1 (`docs/fiszkomat-prompt-audit.md`). Detector previously
inspected only field `m` against 5 hardcoded stopwords — obvious English
in `t/d/i/c/n`, or English `m` text using other vocabulary, slipped
through silently and shipped to paying users.

### What changed

- **`core.py`** — `validate_cards()` now tokenises a concatenation of
  every visible card text field (`t, d, m, i, c, n`) with `[a-z]+` and
  matches against `_ENGLISH_STOPWORDS` (47 entries: be/have/do forms,
  modals, demonstratives, wh-words, English-only prepositions and
  connectors). Threshold preserved at `>=2` total hits. Polish-overlapping
  English-lookalikes (`to/on/do/by/we/i/a`) are explicitly excluded.
- **`tests/test_audit_findings.py`** — the four xfail-strict tests
  registered in commit `538e8a1` now pass as positive pins; `@xfail`
  decorators removed, classes renamed without the `Xfail` prefix,
  section comment rewritten to describe pinned behaviour.

### Verification

```
cd fiszkomat && .venv/bin/python -m pytest tests/ -v
# 147 passed in 0.42s (4 previously xfail now passing as positive pins)
python3 scripts/audit_secrets.py   # exit 0
```

### What's still open from the audit

- #1 (forced tool-use port from invoice_idp) — architectural; deferred.
- #3 (`---` delimiter escape in `chunk_user_prompt`) — small but still
  open.
- #4-#7 (per-upload spend cap, user-facing rejection surfacing, live
  sanity test, threat-model doc) — non-urgent.

---

## 2026-05-16 → 2026-05-17 (overnight) — first test coverage in `tests/`

Project had no `tests/` directory before tonight. Three test files
land covering the pure-unit + sample-asset surfaces. Driven by the
`/goal` overnight protocol (`GOAL.md`) — operator asleep, autonomous
agents fanned out via `Explore` surveys + `general-purpose`
implementation, all commits local-only awaiting morning E2E + push.

### What changed

- **`tests/conftest.py`** — minimal sys.path shim so `pytest tests/`
  works from `fiszkomat/` per the README convention.
- **`tests/test_validation.py`** (39 tests) — pins `validate_cards()`
  behaviour:
  - Dedup is **sha1-based, NOT Levenshtein** (regression test fires
    if fuzzy dedup is ever added).
  - **No min-length** enforcement on card fields, only max
    (200/800/600/600/800 across `t/m/i/c/n`).
  - Polish diacritic preservation (`ą ę ć ł ń ó ś ź ż`).
  - z-bounds (`0-99`, 0 = unknown chapter).
  - English-leakage guard: `>=2` of `the/and/with/of/is` in `m` rejects.
  - `price_for_pages()` tier boundaries (1/50/51/150/151/300/301/500)
    for both standard and quality-pass modes; quality-pass `>=` standard
    at every tier.
- **`tests/test_telemetry.py`** (28 tests) — fences the cost-leak
  fence in `_sanitize_log_lines()` + `_stats_dict()`. Catches
  `$X.XX` and token-count fields (`in= out= cache_r= cache_w=`) before
  they reach public `/status` JSON.
- **`tests/test_sample_decks.py`** (65 tests, parametrised) — pins
  the public sample-deck surface:
  - `_valid_sample_slug()` is a **strict whitelist** against the
    hardcoded `SAMPLE_DECKS` list (returns False, never raises).
  - All 7 deck `.cards.json` files load cleanly with required keys
    + `z` in `[0, 99]`.
  - All 7 `.apkg` files present + non-empty.
  - Cross-check: no orphan files on disk vs `SAMPLE_DECKS` and vice
    versa.

### Tooling note

`pytest` was missing from `fiszkomat/.venv` despite the README
documenting `pytest tests/` as the convention. Installed it tonight
(dev-only, doesn't touch the running `fiszkomat.service` process).
Declared as `[project.optional-dependencies] dev` in `pyproject.toml`
so future clones get it via `pip install -e .[dev]`.

### Subtleties pinned (worth knowing)

- `_load_sample_deck()` (`web.py:793`) silently filters non-dict list
  entries. New tests assert every entry IS a dict — pins intent.
- `_COST_PATTERN` regex has alternation-precedence quirk: leading
  `(?:[,\s])?` binds only to the `$X.XX` alternative, not the
  token-count ones. Tests assert post-collapse output, not the
  intermediate match.
- `price_for_pages(0)` returns tier-1 price (3 PLN), not an error.
  Web layer rejects zero-page uploads separately.

### Verification

`cd fiszkomat && .venv/bin/python -m pytest tests/ -v` → 132/132 in 0.33s.
No production code touched. No restart needed.

### Commits

`4be85ba` (validation + telemetry), `e324ea3` (sample decks).

---

## 2026-05-14 (evening) — v0.5: reviewer UX pass on GF feedback

### Reviewer UX

- **List view** (`Lista fiszek` toggle in new toolbar above the card):
  all cards in a single scrollable column with `#NN` index, drug names,
  group title, and per-card status (`nowa` / `do powtórki` / `za X` /
  `opanowana`). Clicking a row jumps directly into review of that
  card, replacing the previous lack-of-navigation. Same toolbar holds
  the **reset** button.
- **Reset button** (`Resetuj postęp`, with `confirm()` prompt): clears
  `STATE.reviews` entirely (so all counters return to fresh state) but
  preserves `STATE.edits` — card content corrections survive a review
  reset. Edits are user labour; reviews are state to roll back.
- **"Opanowane" tooltip**: small `?` icon next to the stat — hover for
  browser `title=` text, tap on mobile triggers `alert()`. Text:
  *"Karta jest opanowana po 3 dobrych powtórkach (przyciski 'Dobre'
  lub 'Łatwe'). Klik 'Powtórz' cofa licznik."* — answers GF's "co
  znaczy opanowane?" question.
- **"Dziś" semantics fix**: counter was `cards with due <= endOfDay`
  (= cards *planned* for today), which is unintuitive — a fresh deck
  showed `23/23 dziś` and a reset bumped the counter UP. Changed to
  `cards reviewed since midnight` (`r.last >= startToday`). Reset
  correctly takes it to 0 and matches Anki's reading of the same word.
- Keyboard handler (`Space`, `1`-`4`) is no-op in list view and edit
  view so toolbar navigation doesn't compete with review shortcuts.

### Where the changes live

All in one file: `src/fiszkomat/study_html.py`. Pure CSS + HTML + JS
string. No new Python deps; no schema change to `localStorage`
(existing users' state still loads). The
`<script>\n(function(){` marker that `web.py:1199` substring-replaces
for sample-deck card injection is **untouched** — sample routes
(`/study/sample/<slug>`) work identically.

### Deploy

No `pip install -e .` needed. `sudo systemctl restart fiszkomat`
reloads the service and the new string is served on the next request.

---

## 2026-05-14 — v0.4: the long day (production + sample decks + quality pass)

### Production

- Public URL live: `https://fiszkomat.ewwesolutions.work` via Cloudflare
  named tunnel. `FISZKOMAT_PORT` env defaults to 8000, set to **8001**
  in production to avoid collision with Faktomat on the same host.
- **Free-token bypass (`?t=PITONCZYK`) removed** — was an abuse vector
  once the site was publicly tunnelled. Operator + GF buy normally or
  use sample decks.

### Sample decks (the warm-up funnel)

- Landing has a `FISZKI PRZYKŁADOWE` section with **7 curated decks**
  covering most of the WMS farmakologia year:
  Z8 Układ oddechowy (23), Z13 Toksykologia (18), Z15 Antybiotyki +
  antyseptyki (53), Z16 Przeciwwirusowe + przeciwgrzybicze +
  pasożytnicze (37), Z17 Leki układu pokarmowego (92), Z18 Hormony
  rozdz. 16 (38), Z19 Metabolizm Ca + cukrzyca + otyłość (40).
  **296 cards total.**
- Each deck has its own `/study/sample/<slug>` route reusing the
  existing SM-style reviewer; cards inlined as `window.__INLINE_CARDS`
  (avoids the `/jobs/{id}/cards` fetch). `/sample/<slug>/deck` serves
  the `.apkg` directly.
- **Generation pipeline: subagent reads PDF.** Operator's PDFs had
  broken text layers (pypdf returned `\x01` control bytes). Workaround:
  `pdftoppm` renders PDF → PNG (150 DPI), then a general-purpose
  subagent reads every PNG with the Read tool and writes cards JSON
  1:1 with skrypt content. Zero `ANTHROPIC_API_KEY` spend — uses
  Claude Code MAX plan. Token cost across 4 PDFs (Z15/Z16/Z18/Z19):
  ~249k MAX tokens, $0 API budget.
- Sample decks shipped as committed assets:
  `fiszkomat/test_docs/out/zaj<NN>.cards.json` + `.apkg`. Whitelisted
  per-file in `.gitignore`. `SAMPLE_DECKS` in `web.py` is the source
  of truth for landing tabs.

### Picker UX

- Old tabbed picker broke at 7 decks (tabs overflowed at >4). Replaced
  with a **toggle between two views**:
  - **Siatka** (default): CSS grid, 1 col mobile (<520px), 2 col
    tablet+. Each tile = ZAJĘCIA + title + subtitle + count + STUDIUJ
    button + `.apkg` link.
  - **Lista**: compact rows, full-row clickable, count + arrow on the
    right.
  Toggle state persists in `localStorage["fiszkomat-samples-view"]`.
- `fiszkomat` wordmark wrapped in `<a href="/">` on landing + study
  pages. Returns to home from anywhere.

### Generate flow — quality pass

- Click **Wygeneruj fiszki** → modal with **two variants**:
  - **Standardowy** (Haiku 4.5 only).
  - **Standardowy + quality pass** (Haiku → Opus 4.7 review).
    Premium card has a "polecane" badge.
- Modal: paper-cream content + rgba blur backdrop, click-outside / Esc
  cancels. Hidden input `quality_pass` is set on variant pick, form
  POST follows.
- Backend `core.run()` accepts `quality_pass: bool`. After Haiku pass +
  validation, if `quality_pass=True` the deck is sent to a single
  `claude-opus-4-7` call with a "Polish pharmacology professor"
  reviewer prompt — catches wrong drug groupings, mechanism errors,
  indication mismatches, duplicates. Cards without errors are left
  unchanged. Schema-validated again post-review; falls back to Haiku
  output if parse fails (customer never gets nothing).

### Pricing

- Standard tier reduced 5/10/20/35 → **3/5/10/15 PLN**.
- Quality-pass tier is a SEPARATE table (not a flat multiplier), sized
  to never-loss against worst-case Opus 4.7 + Haiku-vision wholesale:
  **5/8/16/25 PLN**. Worst-case margins 25-40% across all tiers;
  standard tier still has 80%+ margin (Haiku floor is cheap).
- The multiplier is not displayed in copy anywhere — customer sees two
  prices, picks one.

### PDF handling

- `_printable_chars()` helper in `core.py` — counts only chars that
  are `isprintable()` or whitespace. Used by `extract_pages` instead
  of `len(t)`. Catches the broken-text-layer case (PDFs that return
  `\x01` bytes pass a naive byte count but yield 0 cards from Haiku);
  now correctly raises `EmptyPdfError` → vision-mode auto-engage.
- Landing has a `/// obsługujemy` block listing the three PDF types
  we accept: native (text layer), scanned (auto OCR via Claude
  vision), broken-text-layer (detected → OCR).
- Vision-mode Haiku is **NOT 3× more expensive** in practice (old
  comment in `PRICE_TIERS` was from Haiku 3 days). Real data: vision
  on 16p Z8 = $0.052, text on 13p Z13 = $0.047. Same ballpark.

### User-facing log sanitization

- `_sanitize_log_lines()` strips `$X.XXXX`, `in=N`, `out=N`,
  `cache_r=N`, `cache_w=N` from log lines before they leave the
  server. Operator's full log preserved at `job.log_lines`.
- `_stats_dict()` drops `api_cost_usd`, token counts. Customer sees
  card count + wall seconds only.

### Polish copy fixes

- "Wsadź → wrzuć", "Polski-pierwszy → polska specyfika", etc. in
  landing — addressed operator's "nie postarałeś się" feedback.
- Contact footer on the landing — operator's email surfaced for
  "talia from a different zajęcia" requests. (Wired in `web.py`,
  gated by `PII_ALLOWLIST_SUFFIXES` in `audit_secrets.py`.)

---

## 2026-05-13 — v0.3: prompt cache + V1.5 in-browser reviewer

- **Prompt cache engaged**: system prompt padded to ~4200 tokens with
  5 more ground-truth exemplars + extended style guide. Verified on
  Haiku 4.5 that chunk N+1 reads from cache.
- **V1.5 in-browser reviewer shipped**: `GET /study/{job_id}` serves a
  self-contained HTML page with a 4-button SM-style review loop
  (fixed intervals tuned for kolokwium prep — 10min / 4h / 1d / 3d),
  inline card editing (essential per operator: small generation
  errors get fixed by the user, not re-generated), localStorage for
  review + edit state, Polish UI, mobile-first responsive. No
  accounts, no backend state for reviews.
- `core.run()` now also writes `<id>.cards.json` alongside `.apkg` so
  the reviewer can fetch the card data. `.apkg` remains the secondary
  CTA for Anki diehards.
- `FISZKOMAT_DEV_MAX_CHUNKS` env var: optional dev-mode cap on chunks
  per generation, used for fast smoke tests (no production effect).

### Stripe Checkout shipped same day

- `POST /jobs` returns either `{job_id, checkout_url}` (default) or
  `{job_id, free: true}` when the request includes the operator's free
  token. Pricing: **5 PLN for ≤200p, 10 PLN for 201–500p** (later
  reduced in v0.4). Cap at 500p enforced server-side. Card-only
  payment (Stripe rejected p24/blik on operator's account).
- `GET /pay/return` verifies the session and kicks off generation.
- `POST /stripe/webhook` is a backup queueing path; refuses with 503
  unless `STRIPE_WEBHOOK_SECRET` is configured.
- `FISZKOMAT_FREE_TOKEN` env var = bookmark URL bypass (`?t=<token>`)
  for operator + GF use without paying themselves. **Removed in v0.4**
  after public tunnelling.
- PDF page count happens before the checkout session is created, so
  pricing is per-doc-accurate.

---

## 2026-05-12/13 — v0.2: Phase 0 CLI + Phase 1 web shell

### Phase 0 — CLI deliverable ✅ SHIPPED

- Operator can run `fiszkomat <skrypt>.pdf` (Windows; only Anthropic
  API call required). `pip install -e .` works.
- Output `.apkg` opens in Anki Desktop cleanly (`Leki-lato.apkg`).
- 159 cards on `Leki-lato-…` skrypt, **100% GF cull-pass acceptance**.
  Operator quote: *"Anki cards are spot on 100% correct amazing I
  love you keep going"*.

**Phase 0 → Phase 1 gate measurements (all green):**

| Gate criterion | Threshold | Actual |
|---|---|---|
| GF accepts deck for real kolokwium revision | yes | yes |
| Cards surviving cull pass | ≥ 70% | ~100% |
| Cost per skrypt | ≤ $2.00 | $0.18 (38p) |
| Generation time | ≤ 5 min | 3 min 53s |
| Diacritics + Polish-only on sample | 100% | 100% |

### Phase 1 — web shell

- FastAPI app in `src/fiszkomat/web.py`. Runnable: `python -m
  fiszkomat.web`.
- `GET /` Polish-language upload form, vanilla HTML, styling matched
  to the deck aesthetic (cream paper, deep red titles, JetBrains
  Mono labels).
- `POST /jobs` multipart PDF upload (30 MB cap, content-type check,
  empty-file check). Writes PDF to `_work/<id>.pdf`, kicks off
  generation in a background executor.
- `GET /jobs/{id}` JSON status: queued / running / done / failed.
  `GET /jobs/{id}/deck` file download of `.apkg`.
- Privacy: source PDF deleted from disk as soon as generation
  finishes. Decks expire after 1 hour.
- In-memory job registry (single-instance — OK for early Phase 1).
- HTTP smoke test passed end-to-end on 2026-05-13 with the same 38p
  skrypt; deck identical to CLI output (158 cards, 1 dedup, $0.18,
  233s).

### Other v0.2 work

- Chunked-attention thesis empirically supported. PDF chunking is
  now `Zajęcia N.` boundary-aware (see `core.py:detect_zajecia`).
- Card-mode question (basic/cloze/both) resolved by ground-truth
  file `fiszki-farmakologia.html`: pharma-group cards with
  `z,t,d,m,i,c`.
- Truncation handling: `max_tokens` raised to 8192 + per-object
  salvage regex for partial responses (`_salvage_card_objects`).

---

## 2026-05-12 — v0.1: initial spec

Original spec drafted, scope set: Polish-medical PDF → Anki via
chunked Sonnet/Haiku. Phase 0 = CLI; Phase 1 = web; Phase 2+ =
distribution + non-pharma expansion. See git history for the
original SPEC.md text.
