# Tender Agent — Phase 0 + 0.5 + 0.6 + 0.7 + 0.8 + 0.9 handoff

**Phase 0 date:** 2026-05-14 evening (autonomous build session)
**Phase 0.5 date:** 2026-05-19 morning (multi-agent SIWZ ingestion pass)
**Phase 0.6 date:** 2026-05-19 afternoon (verifier sub-agent)
**Phase 0.7 date:** 2026-05-19 evening (monitor + ranker; parallel session)
**Phase 0.8 date:** 2026-05-19 evening (UTC display fix + JEDZ Parts II–IV)
**Phase 0.9 date:** 2026-05-19 night (retry-on-verify-error + bundle-join fix + tighter Section A prompt)
**State:** 🟢 End-to-end pipeline ships SIWZ-aware drafts (A–G), runs
the verifier after every draft, and now **auto-retries up to 2 times
when the verifier flags errors** (keeps best). All 3 real samples PASS
on first attempt with the tighter Section A prompt — including Pucki,
which had been a steady-fail sample due to a Haiku drift introduced
by Phase 0.8. **No git push, local commits only.**

## Phase 0.9 TL;DR (2026-05-19 night)

Three concrete fixes after the Phase 0.8 regen exposed real Haiku
drift on the Pucki sample. None of these is a model change — each
closes a specific gap surfaced during the previous test pass.

**1. Retry-on-verify-error wrapper in `cmd_draft`.** New
`--max-draft-retries N` flag (default 2 = up to 3 total attempts):
when the verifier flags `error`-severity findings, regenerate the
draft and re-verify. Stop on the first PASS; if all retries fail,
keep the attempt with the fewest findings (errors first, then warns,
then attempt index for tie-break). No-op when `--no-verify` is set,
with a warning printed.

Output line on retry: `Wrote …/draft.md (attempt 2/3 chosen — fewest
verifier findings)` so the operator can tell at a glance which run
was kept.

**2. Verifier bundle-join fix.** `verify.verify_draft` was joining only
the four legacy sections (A-D) when given an in-memory `DraftBundle`,
silently skipping the Phase 0.8 sections E (JEDZ II), F (JEDZ III),
G (JEDZ IV). Result: the deterministic check claimed firm NIP/REGON
were missing whenever the drafter put them in JEDZ II's identification
table instead of (or in addition to) Section A. Now joins all seven
sections — the retry loop sees the same draft content as a
post-hoc `verify --draft-path` run would.

**3. Tighter Section A prompt.** Phase 0.8's JEDZ Parts II-IV expansion
gave Haiku enough rope to drift Section A into "oświadczenie
spełnienia warunku doświadczenia" on Pucki (which has a heavy
`warunki_udzialu` list). New explicit example sentence in the prompt:
> Section A MUST open with: "Niniejszym oświadczam w imieniu
> <FIRM_LEGAL_NAME>, NIP <FIRM_NIP>, REGON <FIRM_REGON>, że
> wykonawca nie podlega wykluczeniu … pod numerem ogłoszenia
> BZP <BZP_NUMBER>".

Identifiers + BZP number marked obligatory in Section A. Drift on
Pucki resolved on first attempt.

**Test matrix after Phase 0.9:**

| Sample | First-attempt verifier result |
|---|---|
| 2026-BZP-00236579 (Łapy) | PASS — 1 warn (auto name not verbatim, cosmetic) |
| 2026-BZP-00236925 (Miastko) | PASS — 0 findings |
| 2026-BZP-00237383 (Pucki) | PASS — 1 warn (auto name `- ` vs ` — `) |

Per-sample cost (single-attempt path):
~$0.04 Haiku draft + ~$0.01 Haiku verify ≈ **5 grosze end-to-end**.
The retry wrapper adds zero cost when not exercised.

## Phase 0.8 TL;DR (2026-05-19 evening)

Two-part follow-up to Phase 0.6's verifier findings.

**Part 1 — UTC display fix.** `submitting_offers_date` parsed from the
BZP API is timezone-aware UTC. Three call sites (`cli.py:cmd_fetch`,
`cli.py:cmd_draft`, `draft.py:_announcement_block`) were calling
`strftime` directly, displaying 07:00 / 08:00 UTC instead of the
correct Polish wall-clock 09:00 / 10:00 (CEST = UTC+2 in May). New
`draft.fmt_pl_datetime(dt)` helper converts to `Europe/Warsaw` via
stdlib `zoneinfo`; all three call sites now use it. The Phase 0.6
verifier flagged this on every SIWZ-aware sample — first time the
verifier-as-bug-finder paid for itself.

**Bonus discovery:** `verify._announcement_summary` had the *same* UTC
bug — feeding raw ISO to the LLM verifier so it cross-checked the
correctly-rendered Polish-local draft against UTC and flagged the
correct value as wrong. False-positive feedback loop, fixed by also
routing through `fmt_pl_datetime`.

**Part 2 — JEDZ Parts II, III, IV.** Spec §3 step 4 ("Draft") was
Phase-0-limited to JEDZ Część I. Drafts now emit four extra sections:

- **E. JEDZ Część II — Informacje o wykonawcy.** Auto-fills nazwa /
  NIP / REGON / KRS / adres / reprezentant from `FirmProfile`. Status
  MŚP + podwykonawcy / poleganie na zasobach left as
  `[DO UZUPEŁNIENIA: ...]` (specialist judgement, no source-of-truth).
- **F. JEDZ Część III — Powody wykluczenia.** Standardowe oświadczenia
  negatywne wykonawcy: art. 108 ust. 1 pkt 1-4, art. 109 ust. 1 pkt 1
  i 4 i 5–10. Boilerplate per PZP 2023.
- **G. JEDZ Część IV — Kryteria kwalifikacji.** Two paths:
  - SIWZ.warunki_udzialu pusta → forma uproszczona α: jedno
    oświadczenie spełnienia całości (typowe dla trybu podstawowego
    art. 275 pkt 1, krajowy próg).
  - SIWZ.warunki_udzialu niepusta → szczegółowe sekcje A–D z
    kategorii (`kompetencje`, `uprawnienia`, `sytuacja ekonomiczna`,
    `zdolność techniczna`) z cytatem warunku 1:1 + odpowiednim
    `[DO UZUPEŁNIENIA: <środek dowodowy>]` per SIWZ.evidence_required.

Output format spec in `SYSTEM_PROMPT` rebumped from "dokładnie cztery
bloki" to "dokładnie siedem bloków". `DraftBundle` gained
`jedz_czesc_2_md` / `_3_md` / `_4_md` (Optional for backward-compat).
`draft._SECTION_RE` widened from `[A-D]` to `[A-Z]` and from `##` to
`#{1,3}` (Haiku occasionally emits `# A.` despite the example showing
`## A.`). `max_tokens` 8192 → 16384 (JEDZ II-IV adds 4-5k output;
Haiku Part IV on a SIWZ-with-warunki could otherwise truncate).
`draft._user_prompt` unchanged at the call boundary.

**Real bugs Phase 0.8 surfaced on day one** (post-regen on all 3
samples):

| Sample | Finding | Source |
|---|---|---|
| 2026-BZP-00236579 (Łapy) Haiku+SIWZ | 30 dni vs 29 dni math (19 May → 17 June is 29) | LLM verifier |
| 2026-BZP-00237383 (Pucki) Haiku+SIWZ | Firm name typo: `PrykładIT` (missing `z`) — same bug class as Phase 0 | deterministic |
| 2026-BZP-00237383 (Pucki) Haiku+SIWZ | Authority name: `Stamarostwo Powiatowe` (inserted `ma` in `Starostwo`) | LLM verifier |

Both Pucki typos in a single Haiku run. The deterministic check
catches firm-side hallucinations (firm name is in our profile, easy to
verify); the LLM verifier catches authority-side ones (where the
ground truth is in the announcement, not the firm profile).

The Phase 0 "we can't fully eliminate without a post-generation regex
check" line in this file is now **moot** — we have that check, it
works, and Haiku has already produced the bug it was written to
catch.

Total Phase 0.8 spend (3 regen + verify + Sonnet primary): ~$0.10
direct Anthropic. End-to-end (parse → SIWZ → draft → verify) is now
~$0.05-0.10 per Haiku tender, ~$0.20 per Sonnet tender. Margin vs
spec §5's 3-8 PLN tier still 100×+.

## Phase 0.7 TL;DR (2026-05-19 evening)

Added a monitor + fit-score ranker (`src/tender_agent/monitor.py`).
Closes Phase 1 step 2 (Monitor) from `specs.md §3`. No new
dependencies; standalone `python -m tender_agent.monitor` entry point
so it didn't collide with the parallel session's mid-edit `cli.py`.

Heuristic score (per-firm-fit, transparent — easy to tune later):

| Component | Range | Logic |
|---|---|---|
| `cpv` | 0–100 | Main CPV prefix match → 100, additional → 60, else → 0 |
| `deadline` | -100..100 | ≥14d=100, 7-14d=60, 3-7d=30, <3d=0, past=-100 |
| `criterion_bonus` | 0–50 | 100% cena=0, <60% cena=50 (rewards non-price differentiation) |

Default CPV watchlist: **`72,48,30`** (IT services + software packages
+ hardware). CPV 71 was tested 2026-05-19 evening and dropped — most
71* hits are civil engineering / road / bridge work, not IT-adjacent.
HANDOFF Phase 0 mentioned 71 as "civic-IT building" but the actual
71xxxxx tree is architectural/engineering. Default is now correct.

Output: two files alongside each other:

- `<out>.md` — Markdown digest with ranking table + top-N detail
- `<out>.md.jsonl` — one JSON line per scored announcement (for a
  future Discord/email digest cron hook)

Real-world dry-run on the 7-day window ending 2026-05-19:

```
fetched=200 (page-size cap, warned in digest)
matched=19 (CPV 72,48,30)
top 5 (all score=155): Oracle migration, ISMS docs, IT hardware ×2,
                       technical advisor — all genuinely IT
```

**Page-size cap:** BZP API silently caps at 200 items per call. A 7-day
window currently slips under the cap; the watchful Phase 1 follow-up
loops date windows until each returns <200 (Phase 0 HANDOFF "What
padło" item 1 — still open).

**Cost: $0.00.** Monitor is pure-fetch + arithmetic; no LLM calls. The
expensive ops (draft, SIWZ extract, verify) only run after the
operator picks a tender from the digest.

### Hook the digest into a cron (operator-side step)

```bash
# Daily 06:30 CEST on the VM (systemd timer; or crontab if simpler).
# Drops a Markdown + JSONL pair into _samples/monitor/<YYYY-MM-DD>.md
cd ~/playspace/random/tender_agent
.venv/bin/python -m tender_agent.monitor \
    --days 1 \
    --cpv 72,48,30 \
    --firm _samples/curated/firm_demo.json \
    --out _samples/monitor/$(date +%Y-%m-%d).md \
    --limit 20
```

Discord-emit hook (future): tail the new `.jsonl`, send each line as a
Discord embed to the tender_agent-specific webhook (per `specs.md`,
**separate** from `BIGGA` which is invoice_idp's).

## Phase 0.6 TL;DR (2026-05-19 afternoon)

Added a two-tier verifier sub-agent (`src/tender_agent/verify.py`) that
runs after every draft:

1. **Deterministic checks** — pure regex/string, no network. Catches
   the `PrykładIT → PrzykładIT` typo class (single-char deletion +
   single-diacritic strip variants), missing firm NIP/REGON/KRS,
   missing authority NIP/REGON, missing BZP number, and stray
   10-digit tokens that don't match any known identifier.
2. **LLM cross-check** (Haiku 4.5, tool-use-forced JSON) — every
   Section D citation must trace back to `SiwzRequirements`; every
   number/date in A/B/C must reconcile with `TenderAnnouncement`. Only
   flags factual disagreements, not stylistic preferences.

Output: `VerificationReport` (pydantic) → `verification.json` next to
draft + `kind: verify` JSONL line in the cost log. CLI auto-runs after
`draft` unless `--no-verify`; `tender-agent verify <bzp-id>` runs on
existing drafts standalone.

Cost: $0.005-$0.014 per verification at the Phase 0.5 input size
(5k-8k tokens of draft + announcement + SIWZ context). Well inside
the 3-8 PLN tier — adds ~3 grosze to the per-tender total.

**Real bugs the verifier caught on day one** (caveat: small N):

| Sample | Finding | Source |
|---|---|---|
| 2026-BZP-00236579 (Łapy) Haiku+SIWZ | Header shows `2026-05-19 07:00` but Section B + SIWZ say `09:00` | LLM |
| 2026-BZP-00236579 (Łapy) Sonnet+SIWZ | Same `07:00` vs `09:00` header inconsistency | LLM |
| 2026-BZP-00236925 (Miastko) Haiku+SIWZ | Header `08:00` vs SIWZ `10:00` | LLM |
| 2026-BZP-00237383 (Pucki) Haiku+SIWZ | Authority NIP `5871707828` entirely absent from draft | deterministic |
| 2026-BZP-00237383 (Pucki) Haiku+SIWZ | Draft cited firm NIP `5252888777` in the *zamawiający* block | LLM |
| 2026-BZP-00237383 (Pucki) Haiku+SIWZ | Header `08:00` vs SIWZ `10:00` (same UTC-display bug) | LLM |

The `07:00` / `08:00` thread is a **single pipeline-level bug**: BZP
API returns `submittingOffersDate` in UTC; `cmd_draft`'s rendered
header uses `submitting_offers_date.strftime('%Y-%m-%d %H:%M')` with
no timezone conversion, so Polish local times (CEST = UTC+2 in May)
display two hours early. Phase 0.5 LLM-text bodies render this
correctly (Polish word-form date is generated by the LLM from a
different prompt section, which uses the body of the announcement
where times are already local). Fix is one line in `cli.py:cmd_draft`.
Tracked as a Phase 0.7 carryover, not blocking.

**Synthesized typo test** — copying a real draft and replacing every
`PrzykładIT → PrykładIT` (the same bug class as the original Phase 0
fail-case in this HANDOFF) yielded 3 deterministic errors. Confirms
the verifier catches the original motivating issue.

## Phase 0.5 TL;DR (2026-05-19)

Added: downloaders for the two real procurement platforms (e-Zamówienia
mp-client + logintrade), pymupdf+stdlib SIWZ extractor module with a
new `SiwzRequirements` pydantic model, and SIWZ-context injection in
the drafter prompt. Section D switches from "specialist must verify..."
prose to concrete citations: termin związania, wadium absence, kary
treatment, kryteria oceny formula, dokumenty checklist. **The drafter
now reads the actual procurement spec, not just the 4-page announcement
summary.**

Per-SIWZ cost stays in the $0.03-0.05 range (Haiku 4.5 extraction +
Haiku draft). Per-tender end-to-end (announcement parse → SIWZ download
→ SIWZ extract → SIWZ-aware draft) is ~$0.07 Haiku or ~$0.15 Sonnet —
well inside spec §5's 3-8 PLN tier (still 100-200× margin).

## Phase 0.5 TL;DR — what partner can look at after this pass

```
tender_agent/_samples/2026-BZP-00236579/        ← primary IT case (SPZOZ Łapy)
  raw.json                       ← BZP API response (Phase 0)
  body.html                      ← announcement HTML (Phase 0)
  parsed.json                    ← TenderAnnouncement (Phase 0)
  draft_haiku.md                 ← Phase 0 draft, no SIWZ context
  draft_sonnet.md                ← Phase 0 draft, no SIWZ context
  siwz/                          ← Phase 0.5: SIWZ + załączniki from e-Zamówienia mp-client
    SPECYFIKACJA WARUNKÓW ZAMÓWIENIA (SWZ).docx
    ZAŁ. NR 1 - FORMULARZ OFERTOWY.docx
    ... 8 more załączniki + manifest.json
  siwz_extracted.json            ← Phase 0.5: structured SiwzRequirements
  draft_haiku_with_siwz.md       ← Phase 0.5 draft, SIWZ-aware
  draft_sonnet_with_siwz.md      ← Phase 0.5 draft, SIWZ-aware ← show this one to the partner
  draft_*.verify.json            ← Phase 0.6: VerificationReport per draft
```

Same shape for the 2 other samples (`2026-BZP-00236925`, `2026-BZP-00237383`)
with their respective platforms (e-Zamówienia mp-client and logintrade).

**Single most useful artifact for partner review:** the diff between
`draft_sonnet.md` (Phase 0) and `draft_sonnet_with_siwz.md` (Phase 0.5)
on SPZOZ Łapy. Same letter, same JEDZ, but Section D goes from
"specialist must check the SIWZ for X, Y, Z" to "okres związania
ofertą = 29 dni do 17.06.2026; wadium nie wymagane; kryteria: cena 100%
metodyką (Cn/Cb)×100; realizacja 60 dni od podpisania umowy, nie później
niż 6.07.2026; **uwarunkowane przyznaniem środków publicznych** —
postępowanie unieważnione przy nieuzyskaniu." Every line citable to SIWZ.

## Phase 0.5 — what was added (2026-05-19)

Three new modules (fanned out as parallel sub-agents, synthesized in
this session — no overlap, all `mypy --strict` clean):

| File | Role |
|---|---|
| `src/tender_agent/siwz_ezamowienia.py` | e-Zamówienia mp-client downloader. Uses the undocumented `/mp-readmodels/api/Search/GetTender?id=…` + `/DownloadDocument/<tid>/<doc>` JSON API (public, no auth). Skips archived/superseded versions. Returns + persists `manifest.json` with sha256+size+content-type per file. |
| `src/tender_agent/siwz_logintrade.py` | logintrade.net downloader. Plain SSR PHP — parses `<span class="attachment-name">` siblings, follows `DocumentService,getAttachmentUnlogged,<token>.html` links. Honors RFC 5987 + quoted-string `Content-Disposition`. Detects login walls cleanly (HTML response on the download endpoint → `status: gated` instead of overwriting as PDF). |
| `src/tender_agent/siwz_extract.py` | PDF (pymupdf) + DOCX (stdlib `zipfile` + `xml.etree`) → `SiwzRequirements` pydantic model via Claude Haiku 4.5 with tool-use-forced JSON. No new deps; reuses cost-log JSONL shape (`kind: siwz_extract`). |

Plus the drafter wiring:

- `src/tender_agent/draft.py` extended with `_siwz_block()` renderer +
  optional `siwz: SiwzRequirements | None` parameter on
  `draft_for_announcement(...)`. System prompt updated to instruct
  Section D to switch from "verification checklist" to "concrete
  citations" when SIWZ context is present; Phase 0 behavior preserved
  when `siwz=None`. `max_tokens` raised 4096 → 8192 (Sonnet hit the
  4096 ceiling mid-Section-D on the SPZOZ Łapy sample at 4096).
- `src/tender_agent/cli.py` gains `--siwz <path>` and `--siwz auto`
  (latter auto-loads `<sample>/siwz_extracted.json` if present).
- `src/tender_agent/models.py` gained `SiwzRequirements` (+ 6 supporting
  submodels: SiwzWarunek/Kryterium/KaraUmowna/Terminy/Wadium/Kontakt).

### What the 3 SIWZ ingestions produced (factual)

| Sample | Platform | SIWZ format | Pages/blocks | Extract cost | Draft cost (Haiku) | Notes |
|---|---|---|---|---|---|---|
| 2026-BZP-00236579 (SPZOZ Łapy) | e-Zamówienia mp-client | .docx | 315 paragraph blocks (≈57k chars) | $0.031 | $0.023 | Primary IT case — wdrożenie autoryzacji domenowej. Sonnet redraft also: $0.083. |
| 2026-BZP-00236925 (Szpital Miastko) | e-Zamówienia mp-client | .pdf (Część I IDW) | 24 | $0.037 | $0.019 | Cleanest extraction — proper IDW PDF. |
| 2026-BZP-00237383 (Powiat Pucki) | logintrade | .pdf | 25 | $0.047 | $0.025 | Tryb art. 275 pkt 2 — no JEDZ, no wadium (correctly returned null). |

Phase 0.5 total session spend: **~$0.27** (3 extractions + 4 redrafts).
Anthropic balance impact: ≈27 cents.

### Confirmed: no-fabrication rule held

Spot-checked Powiat Pucki extraction against the source SIWZ — the
extractor correctly returned `wadium=None`, `kary_umowne=[]`, and
`jedz_scope=[]` because that tender uses the simplified PZP art. 275
pkt 2 procedure (no JEDZ requirement, no wadium per "Brak
przewidzenia"). The drafter, given that input, wrote "wadium nie jest
wymagane" — instead of inventing one, which is what a less-disciplined
prompt would do.

### What's still open after Phase 0.5 / 0.6 / 0.7 / 0.8

1. **Legacy `.doc` files** (binary Word 97-2003) appear in older logintrade attachments. Extractor doesn't support them — `siwz_extract.extract_text(…)` raises on unknown suffix. Phase 1 fix: either depend on `libreoffice --headless --convert-to docx` or add `olefile`-based binary `.doc` parsing.
2. **Prompt cache not warming.** Both `siwz_extract.py` and `draft.py` declare ephemeral cache blocks but `cache_creation_tokens=0` on every call — the system prompt is below Anthropic's 1024-token cacheability minimum. Either bloat the system prompt with deterministic boilerplate or accept the no-cache cost (still well inside budget). `verify.py` inherits the same situation. After Phase 0.8 the drafter system prompt grew substantially (JEDZ II-IV instructions) — recheck cache thresholds; it may now qualify.
3. ~~**JEDZ Parts II–IV**~~ — landed Phase 0.8.
4. ~~**Verifier sub-agent**~~ — landed Phase 0.6.
5. **Partner firm profile** still demo (`PrzykładIT sp. z o.o.`). Replacing this is the single biggest unlock for partner-review value.
6. ~~**CPV scope broadening**~~ — landed Phase 0.7 (default now `72,48,30`).
7. ~~**Monitor cron + ranking**~~ — landed Phase 0.7.
8. ~~**UTC-vs-local time** in `cmd_draft` header~~ — landed Phase 0.8 via `draft.fmt_pl_datetime` helper. Verifier feed also fixed.
9. **Haiku schema violations under draft pressure** — on the synthesized typo case (with PrykładIT swapped throughout), Haiku-as-verifier sometimes returned `findings` as a JSON-encoded string instead of an array. `verify.py` has a defensive parse-and-coerce path (one info-finding when this happens) but the underlying issue is at the model layer; switching the verifier to Sonnet 4.6 would likely eliminate it at ~3× the per-call cost.
10. ~~**Haiku occasionally typos firm + authority names** under JEDZ-II-IV draft pressure~~ — landed Phase 0.9 (retry wrapper + bundle-join fix + tighter Section A prompt). All 3 samples PASS on first attempt now.
11. **Page-size cap on BZP API** — `monitor.py` already warns on `fetched=200`; Phase 0.7 follow-up wants a date-window loop that returns <200 per call (per Phase 0 HANDOFF "What padło" item 1).
12. **JEDZ Part II MŚP status** + podwykonawcy / poleganie na zasobach — drafter writes `[DO UZUPEŁNIENIA]`, correct for now. Could be extended in `FirmProfile` with a `msp_status: bool | None` + `subcontractors_allowed: bool` to autofill more.

## What works

1. **BZP public read API access** — `https://ezamowienia.gov.pl/mo-board/api/v1/notice`
   is no-auth, no-OAuth, no integration paperwork. Returns full
   announcement metadata **plus** the entire HTML body (sections SEKCJA
   I-IX with stable numeric prefixes — easy to parse). The other
   `api.ezamowienia.gov.pl` host is OAuth-gated; we never need it for
   monitor + draft. Skipping the dev-portal-registration path saves
   1-2 days of paperwork in spec §6.
2. **Parser** maps API + HTML 1:1 to a `TenderAnnouncement` pydantic model
   covering 20+ fields: NIP, REGON, postcode, street, role, CPV main +
   additional, criteria with weights, participation conditions, full
   evidence requirements, deadline. Robust to multiple HTML layouts
   (span-in-h3, p-after-h3, raw text-tail).
3. **Drafter** (Haiku 4.5 with prompt-cached system block) produces all
   four sections at $0.012 average, 19s wall. Generated drafts:
   - Cite the BZP number and date 1:1
   - Use proper Polish kancelaryjny formulas ("uprzejmie", "niniejszym
     oświadczam", "w odpowiedzi na ogłoszenie")
   - Cite art. 108 ust. 1 + art. 109 PZP correctly in Section A
   - Auto-populate JEDZ Część I (I.1 / I.2 / I.3 / I.4 sub-blocks)
     from announcement metadata
   - Use **słownie** dates ("11 maja 2026 roku") in the formal letter,
     ISO dates in the structured JEDZ
   - Flag gaps via `[DO UZUPEŁNIENIA: <what's missing>]` rather than
     hallucinating
4. **Sonnet 4.6 quality lift** — same prompt, ~5× cost, ~3× wall: better
   structure (proper letter headers), more complete clauses (contract
   acceptance, validity period), auto-computed defaults (e.g. "30 dni
   od terminu składania ofert, tj. do 18 czerwca 2026"). Worth the
   bump for the **final review draft partner sees**; Haiku is good
   enough for first-pass monitor → screen.
5. **Cost target safely met.** Spec §5 said 3-8 PLN per drafted tender.
   At today's rates:
   - Haiku 4.5: **~5 grosze / draft** (250× margin against 5 PLN tier)
   - Sonnet 4.6: **~25 grosze / draft** (50× margin against 5 PLN tier)
   Even running both flavors on every drafted bid is < 1 PLN
   per-tender.
6. **Cost logging** — every API call appends one JSONL line to
   `_logs/<date>.jsonl` with model id, input tokens, cache hits, output
   tokens, wall-seconds, USD cost estimate. Easy to inspect:
   `cat _logs/*.jsonl | jq -s 'group_by(.model) | ...'`.

## What padło / surprised us

1. **API page size capped at 200 silently.** No documented `Page` /
   `PageNumber` / `PageIndex` parameter works — passing them just
   returns the same first page. To fetch older announcements you have
   to narrow `PublicationDateFrom` / `PublicationDateTo` until the
   range is <200 items. Each "1-day-wide search" returns 20–80 items in
   practice. **Fix for Phase 1:** loop date windows, dedupe by
   `bzpNumber`.
2. **CPV 72\* (IT services) is rare** — only 13 hits in the last 14
   days; in the last 3 days, 1 hit. Most IT-flavoured procurement
   lives under CPV 48 (Pakiety oprogramowania), 30 (Sprzęt
   komputerowy), 32 (Aparatura sieciowa), and 71 (Architectural — for
   civic-IT building projects). **For the partner firm profile we'll need
   to scope CPV prefixes broader than just 72** — likely 48 + 30 + 72
   + selected 32xxxxx codes.
3. **NIP vs REGON labelling** was a real bug in the first run. The
   API field `organizationNationalId` is the **NIP** (10-digit), while
   the HTML 1.4) field carries the **REGON** (9- or 14-digit) — they
   are different identifiers for the same entity. Initial prompt didn't
   distinguish, model defaulted to "REGON" label on the NIP value.
   Fixed in `models.py` + `parse.py` + prompt — see commit log.
4. **Firm name typos** — one Haiku run produced `PrykładIT` instead
   of `PrzykładIT` (dropped one letter). Spurious LLM error;
   reinforced in updated prompt with "use the **dokładnie** stringa
   podanego w danych wejściowych" + listed Polish diacritics
   explicitly. Re-run on the same case rendered correctly; can't fully
   eliminate without a post-generation regex check. **Open Phase 1
   work:** add a verifier sub-agent (per spec §3 step 5).
5. **No SIWZ PDF ingestion yet.** The announcement HTML covers the
   ogłoszenie (the 4-page summary). The actual 30-80 page SIWZ
   (Specyfikacja Warunków Zamówienia) lives on the contracting
   authority's purchasing platform (e.g. `platformazakupowa.pl/pn/...`).
   That's where the line-by-line requirements live — and where the
   spec §3 "score eligibility" step needs to look. **Phase 0 prototype
   intentionally skipped this**; Section D ("Uwagi szkicownika") in
   every draft flags it as a gap for the specialist. Phase 1 needs:
   crawler for the linked platform, PDF text extraction with pymupdf,
   per-section parser.
6. **JEDZ Parts II–IV not generated.** Phase 0 spec said "Część I" only;
   Section B drafts that and the model itself flags the rest as TODO.
   Phase 1: extend the prompt or split into sub-agents per JEDZ Part.
7. **The third fail-case (`2026/BZP 00237383` — Powiat Pucki GIS bazy
   ewidencji)** was an interesting stress test: very domain-specific
   (geodezja + IT), CPV 72320000-4 "Usługi bazy danych". Model
   handled the GIS-specific terminology without inventing facts —
   used the announcement's own vocabulary, flagged the precision of
   the "dokumentacja stanowiąca podstawę zawiadomienia wydziału ksiąg
   wieczystych" requirement as something specialist must confirm.
   Encouraging — no nonsense generation under domain pressure.

## What's deliberately out of scope for Phase 0

Everything from `specs.md` §3 beyond steps 1 (Ingest) + 4 (Draft):

- §3 step 2 **Monitor** (daily cron over BZP + TED matching firm CPV
  profile) — Phase 1.
- §3 step 3 **Score eligibility** (parse SIWZ requirements, cross-check
  against firm profile, flag blockers/near-misses) — Phase 1, needs
  the PDF crawler + ranking sub-agent.
- §3 step 5 **Verify** (review draft against SIWZ requirements one
  more time, flag gaps) — Phase 1, depends on SIWZ extraction.
- §3 step 6 **Hand off** (dashboard, DOCX/PDF export, e-Zamówienia
  upload helper) — Phase 1+ once we know the draft pipeline holds up.
- §3 step 7 **Track** (post-submission status, capture results) — Phase 2.

The Phase 0 prototype validates one thing only: **can the LLM produce
draft sections that a Polish-procurement specialist would start from
instead of opening a blank document?** That gate is roughly met on
3 announcements; the §8 blind-replay against 5 real prior bids is the
proper validation, which requires the partner firm's historical data we
don't have yet.

## Cost model (real numbers, not the spec estimate)

| | Haiku 4.5 | Sonnet 4.6 |
|---|---|---|
| Avg cost / draft | $0.012 | $0.060 |
| Avg wall / draft | 19s | 59s |
| Per 100 drafts | $1.20 | $6.00 |
| Margin vs 5 PLN tier | 250× | 50× |
| Margin vs 1499 PLN/mo Pro tier (unlimited drafts, assume 50/mo) | 5000× | 1000× |

Even with both flavours run on every bid (Haiku monitor pass + Sonnet
review pass), cost-per-tender is < $0.10 — comfortably inside spec §5.

## How to run (operator wake-up checklist)

```bash
# 1. Show recent IT-procurement announcements (CPV 72 default)
cd ~/playspace/random/tender_agent
.venv/bin/python -m tender_agent.cli fetch-it --days 7 --limit 20

# 2. Cache one specific announcement
.venv/bin/python -m tender_agent.cli fetch '2026/BZP 00236579'

# 3. Generate draft (Haiku 4.5 default — fast, cheap)
.venv/bin/python -m tender_agent.cli draft '2026/BZP 00236579'

# 4. Higher-quality variant (Sonnet 4.6)
.venv/bin/python -m tender_agent.cli draft '2026/BZP 00236579' \
  --model claude-sonnet-4-6

# 5. With custom firm profile JSON (replaces the demo PrzykładIT)
.venv/bin/python -m tender_agent.cli draft '2026/BZP 00236579' \
  --firm path/to/partner_firm.json

# 6. SIWZ-aware draft + auto-verification (Phase 0.5 + 0.6)
.venv/bin/python -m tender_agent.cli draft '2026/BZP 00236579' \
  --siwz auto

# 7. Verify an existing draft standalone (Phase 0.6)
.venv/bin/python -m tender_agent.cli verify '2026/BZP 00236579' \
  --draft-path _samples/2026-BZP-00236579/draft_sonnet_with_siwz.md \
  --siwz auto

# 8. Verify deterministic-only (no LLM call) — fast, free, catches typos
.venv/bin/python -m tender_agent.cli verify '2026/BZP 00236579' \
  --draft-path _samples/2026-BZP-00236579/draft.md \
  --skip-llm
```

Output lands in `_samples/<flat-id>/draft.md` and
`_samples/<flat-id>/verification.json`. Cost log in `_logs/<date>.jsonl`
(JSONL entries with `kind`: `draft` / `siwz_extract` / `verify`).

The Anthropic API key is auto-loaded from `tender_agent/.env` first,
then `invoice_idp/.env` as fallback. No new key needed.

## Files

```
tender_agent/
├── HANDOFF.md                          ← this file
├── specs.md                            ← unchanged, parent spec
├── pyproject.toml
├── .env.example
├── .gitignore
├── _logs/                              ← cost JSONL (gitignored except .gitkeep)
├── _samples/
│   ├── curated/firm_demo.json          ← fictional firm profile for prototype
│   ├── 2026-BZP-00236579/              ← primary IT case (Łapy hospital domain auth)
│   │   ├── raw.json
│   │   ├── body.html
│   │   ├── parsed.json
│   │   ├── draft_haiku.md
│   │   ├── draft_sonnet.md             ← show this to the partner
│   │   └── draft.md                    ← latest run
│   ├── 2026-BZP-00237383/              ← fail-case 1: Powiat Pucki GIS bazy
│   └── 2026-BZP-00236925/              ← fail-case 2: Szpital Miastko maintenance
├── src/tender_agent/
│   ├── __init__.py
│   ├── models.py                       ← TenderAnnouncement / FirmProfile / DraftBundle / SiwzRequirements / VerificationReport / Finding
│   ├── fetch.py                        ← BZP API + caching
│   ├── parse.py                        ← HTML → TenderAnnouncement
│   ├── draft.py                        ← Anthropic SDK + prompt + cost logging
│   ├── siwz_ezamowienia.py             ← e-Zamówienia mp-client SIWZ downloader (Phase 0.5)
│   ├── siwz_logintrade.py              ← logintrade SIWZ downloader (Phase 0.5)
│   ├── siwz_extract.py                 ← PDF/DOCX → SiwzRequirements (Phase 0.5)
│   ├── verify.py                       ← Deterministic + LLM verifier (Phase 0.6)
│   └── cli.py                          ← fetch-it / fetch / draft / verify
└── tests/                              ← empty for now — Phase 1 priority
```

Total code: ~3.3k lines including comments. The drafter prompt
(`src/tender_agent/draft.py`) is the single most leveraged file;
iterate there first. Second-most: `verify.py`'s LLM system prompt —
tightening it without making it noisy is the central judgment call.

## Decision points for you / partner

1. **Partner firm profile** — replace `_samples/curated/firm_demo.json`
   with real data:
   - Legal name + short name
   - NIP / REGON / KRS
   - Address
   - Person who signs bids (full name + function)
   - Contact email + phone

   That alone unlocks meaningful partner-review on a draft. Until that
   profile lands, every draft has the fictional `PrzykładIT sp. z o.o.`
   in it — easy to dismiss as obviously-not-our-firm.

2. **CPV prefix scope** — what categories does the partner firm bid on?
   Currently `iter_recent_it` defaults to `72*` (IT services). If the
   firm bids on e.g. 48000000 (software packages) + 71300000 (engineering
   services) we widen the monitor scope.

3. **Sonnet for partner review, Haiku for everything else?** That's my
   default recommendation. Costs $0.07 per "show the partner a real draft"
   vs $0.012 for a screening draft. If you want to be more conservative,
   you can flip to all-Haiku and only Sonnet on the final pre-submit
   pass — depends on whether the partner has time to review Haiku-quality
   drafts or only wants Sonnet-quality.

4. **Should the agent score eligibility / surface bid-worthiness?**
   Phase 0 doesn't — every announcement passed to `draft` gets drafted.
   Spec §3 step 2-3 wants ranking before drafting. Phase 1 priority?

## Token / API consumption this session

```json
{
  "n_calls": 7,
  "haiku_4_5": {"n": 6, "avg_cost": "$0.012", "avg_wall": "19.2s"},
  "sonnet_4_6": {"n": 1, "cost": "$0.060", "wall": "58.7s"},
  "total_session_cost_usd": 0.133
}
```

Anthropic balance impact: ~13 cents of the $17 starting balance.
Plenty of headroom to redraft, iterate the prompt, or run more
fail-cases. Each new draft against this codebase is ~5 grosze.

## Open prompt-engineering wishlist (didn't get to it tonight)

- Section-A oświadczenie should match more recent (2023 / 2024)
  amendments of the PZP — the current version cites 2023 wording but
  doesn't paginate art. 108 enumeration. A specialist will polish.
- Section-C address block format is sometimes wrapped, sometimes
  inline — would benefit from explicit "use this address format"
  example in prompt.
- Section-D ("uwagi") would ideally compile a clean checklist of
  what specialist needs to verify, sorted by importance. Currently
  it's prose paragraphs — easy to skim but harder to action.

## What I didn't touch (per goal constraint)

`fiszkomat/`, `invoice_idp/`, `marketing/` were off-limits to avoid
stepping on the parallel Claude session. I did `git fetch` once
during the session — saw 3 new Dependabot branches (pip updates from
Friday's dependabot.yml landing) and noticed the parallel session
shipped: friendly Polish error messages on `invoice_detail_stub.html`
+ a Re-ekstrakcja button. Both untouched here.

## Local commits (no GitHub merge per goal)

```
todo before sleep — checkpoint commit selectively from tender_agent/ only:
git add tender_agent/
git commit -m "tender_agent: Phase 0 prototype end-to-end (local checkpoint)"
```

Other Claude's uncommitted invoice_idp/* edits stay in the working
tree, untouched, ready for them to commit when they're ready.
