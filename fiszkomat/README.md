# fiszkomat — Polish-medical skrypt → Anki

Drop a long Polish farmakologia skrypt (PDF), get a generated Anki
deck sized for kolokwium use. Chunked Haiku generation aligned to
`Zajęcia N.` headers so attention doesn't degrade past page ten.
Optional Claude Opus 4.7 quality pass for fact-checking the deck.
Scanned PDFs are handled via Claude vision fallback. Per-document
pricing (3-15 PLN by length tier), no subscription.

**Live:** [fiszkomat.ewwesolutions.work](https://fiszkomat.ewwesolutions.work)
**Status:** 🟢 Phase 1 LIVE — paid checkout end-to-end. 7 curated
sample decks on the landing (antybiotyki, toksykologia, układ
oddechowy / pokarmowy, hormony, metabolizm wapnia + cukrzyca +
otyłość — 296 cards total) as the warm-up funnel.

## Stack

Python 3.11 · FastAPI · Anthropic SDK · genanki (`.apkg` packing) ·
Stripe · pypdf + pdfplumber + `pdftoppm` (for the OCR/vision path).
No database — job state is on-disk in `_work/`; SM-style review
state is `localStorage` in the browser.

## How it runs

Production runs as a systemd unit `fiszkomat.service` on the Hyper-V
Ubuntu 24.04 VM (`~/playspace/random/fiszkomat/.venv/`); cloudflared
inside the VM proxies `fiszkomat.ewwesolutions.work` →
`localhost:8001`. Full VM topology in
[`docs/vm-migration-2026-05-14.md`](../docs/vm-migration-2026-05-14.md).

### Production update (on the VM)

```bash
cd ~/playspace/random/fiszkomat
git pull origin main
source .venv/bin/activate && pip install -e .
sudo systemctl restart fiszkomat
sudo journalctl -u fiszkomat -f
```

### Local dev

```bash
cd fiszkomat
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e .

cp .env.example .env                # then fill in ANTHROPIC_API_KEY + Stripe keys
export FISZKOMAT_HOST=127.0.0.1     # or 0.0.0.0 if exposing on LAN
export FISZKOMAT_PORT=8001
python -m fiszkomat.web
```

Then `http://localhost:8001` in a browser.

For a one-off CLI run without the web layer:

```bash
fiszkomat path/to/skrypt.pdf        # → writes <id>.apkg + <id>.cards.json
```

## Tests

```bash
pytest tests/                       # unit tests, no infra needed
```

The reviewer UI is self-contained — open `/study/{job_id}` (paid jobs)
or `/study/sample/{slug}` (curated decks) to drive the SM-style review
loop manually. The whole reviewer is one Python string in
`src/fiszkomat/study_html.py` (CSS + HTML + JS); no build step.

## In-browser reviewer (`/study/...`)

A self-contained Anki-flavored reviewer; no accounts, all state in
`localStorage` keyed per job or per sample slug.

- **Front of card** = drug names only (test: *which group is this?*).
  **Back** = group title + mechanism + indications + contraindications
  + (optional) side effects.
- **Four review buttons** with fixed intervals tuned for kolokwium prep:
  `Powtórz` 10 min · `Trudne` 4 godz · `Dobre` 1 dzień · `Łatwe` 3 dni.
  Keyboard: `Space`/click flips, `1`/`2`/`3`/`4` reviews.
- **Stats bar**: `<n> fiszek razem` · `<n> dziś` (reviewed today, midnight
  rollover) · `<n> opanowane` (count of cards with ≥3 successful
  `Dobre`/`Łatwe` reviews; `Powtórz` decrements the counter).
- **Toolbar**: `Lista fiszek` toggles a per-card overview (status per
  row: `nowa` / `do powtórki` / `za X` / `opanowana`; click jumps into
  that card). `Resetuj postęp` clears review state but **keeps inline
  edits** to card content.
- **Inline edit** (`Edytuj kartę`): per-card overlay stored in
  `STATE.edits` — survives reset, doesn't touch the source `.apkg`.
- `localStorage` shape: `{ reviews: {idx: {due, last, count}}, edits: {idx: {t, d, m, i, c, n}} }`
  under key `fiszkomat-state-<job_id>` or `fiszkomat-sample-<slug>`.

## Deeper reading

- [`SPEC.md`](SPEC.md) — full product spec (v0.4): pricing tiers,
  card schema, chunking algorithm, Phase 0 / Phase 1 deliverables,
  unit economics, risks.
- [`docs/vm-migration-2026-05-14.md`](../docs/vm-migration-2026-05-14.md)
  — runbook for the systemd-on-VM deployment.

## Curated sample decks (shipped in tree)

`test_docs/out/zaj*.apkg` + `.cards.json` — 7 hand-vetted decks used
both as the landing-page warm-up funnel and as integration-test
fixtures. Source PDFs (`test_docs/*.pdf`) are gitignored — they're
copyrighted Polish-medical lecture material.
