# tender_agent — Polish public-procurement bid drafter

A Phase 0 prototype that pulls a real BZP / e-Zamówienia announcement
and produces a Markdown bundle a Polish procurement specialist can
start from instead of a blank document. Targets the niche where
generic LLMs leave most of the work on the table: kancelaryjny Polish
business register, citation-precise references to PZP articles,
auto-populated JEDZ Część I.

**Status:** 🟡 Phase 0 prototype — 1 end-to-end working bid draft on a
real announcement + 2 fail-cases captured. **Not yet usable in
production.** No SIWZ PDF ingest, no JEDZ Parts II-IV, no monitoring
loop, no firm-profile UI. See `HANDOFF.md` for the honest writeup.

**Customer / validation gate:** a Polish IT-procurement firm acts as
design partner and named V1 customer. Phase 0 §8 validation (blind
replay of 5 prior bids against the partner's review) is the next gate;
this prototype is the build artefact that gate runs against.

## Stack

Python 3.11+ · Anthropic SDK (direct API, not Bedrock for the
prototype) · `httpx` · BeautifulSoup4 · pydantic v2 · pymupdf (for
Phase 1 SIWZ PDF parsing). pytest + mypy `--strict` clean.

## How it works

```
BZP API  ─►  fetch.py  ─►  raw.json + body.html
                            │
                            ▼
                          parse.py  ─►  TenderAnnouncement (pydantic)
                                          │
              firm_profile.json ─────────┤
                                          ▼
                                        draft.py  ─►  DraftBundle:
                                                       A) Oświadczenie
                                                       B) JEDZ Część I
                                                       C) szkic listu
                                                       D) uwagi szkicownika
```

Each stage is deterministic and unit-tested except `draft.py`'s LLM
call (smoke-tested against real announcements). Drafter prompt is
prompt-cached so re-runs are ~70 % cheaper after warm-up.

## Run

### One-time setup

```bash
cd tender_agent
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
# Edit .env to set ANTHROPIC_API_KEY (or rely on invoice_idp/.env fallback)
```

### Daily commands

```bash
# Browse recent IT-procurement (CPV 72*) — default last 7 days
python -m tender_agent.cli fetch-it
python -m tender_agent.cli fetch-it --days 14 --limit 50 --cpv-prefix 48

# Cache one announcement by bzpNumber
python -m tender_agent.cli fetch '2026/BZP 00236579'

# Generate draft (Haiku 4.5 — fast, ~5 grosze)
python -m tender_agent.cli draft '2026/BZP 00236579'

# Higher-quality variant for partner review (~25 grosze)
python -m tender_agent.cli draft '2026/BZP 00236579' \
  --model claude-sonnet-4-6

# Replace the demo firm profile with the real partner firm
python -m tender_agent.cli draft '2026/BZP 00236579' \
  --firm _samples/curated/firm_partner.json
```

Outputs:
- Cached fetch in `_samples/<flat-id>/raw.json` + `body.html` + `parsed.json`
- Generated draft in `_samples/<flat-id>/draft.md`
- Per-call cost line in `_logs/<YYYY-MM-DD>.jsonl`

### Tests

```bash
python -m pytest tests/                       # 31 tests, ~0.5 s, no network
python -m mypy --strict --ignore-missing-imports src/tender_agent/
```

The parametrized real-sample tests skip cleanly when
`_samples/<id>/raw.json` isn't cached locally; the synthetic-fixture
tests in `tests/fixtures/minimal_announcement.html` always run and
cover every parser layout path (A: span-in-h3, B: p sibling, C: raw
text tail).

## Cost model

Real per-draft costs measured on three live announcements during
Phase 0:

| Model           | Per draft | Wall  | Margin vs spec §5 5-PLN tier |
|---|---|---|---|
| Haiku 4.5       | ~$0.012   | ~19 s | ~250×                        |
| Sonnet 4.6      | ~$0.060   | ~59 s | ~50×                         |

Recommendation: Haiku for monitor → first draft → user screening,
Sonnet for the final pre-submit polish that the partner firm or
another bidding specialist reviews.

## Deeper reading

- [`specs.md`](specs.md) — full product spec (V1 build plan,
  pricing tiers, distribution channels, 90-day success criteria).
- [`HANDOFF.md`](HANDOFF.md) — Phase 0 build-session notes: what
  works, what padło, decision points for the operator + design
  partner, per-draft cost in real numbers.

## What's deliberately not here yet

All Phase 1+ items from `specs.md` §3 steps 2-7:
- Monitor cron + CPV-eligibility ranking
- SIWZ PDF ingest from contracting-authority platforms (e.g.
  `platformazakupowa.pl/pn/...`)
- JEDZ Parts II-IV (only Part I in Phase 0)
- Verifier sub-agent pass before final draft
- Dashboard + DOCX/PDF export + e-Zamówienia upload helper
- Post-submission status tracking

Phase 0 validates one thing: **can the LLM produce a draft that a
real procurement specialist would start from instead of a blank
page?** That answer informs whether Phase 1 is worth building.
