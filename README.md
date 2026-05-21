# Ewwe Solutions

A small studio shipping AI tools for Polish-language vertical problems —
the niches where generic large-language-model products break down on
language, regulation, or domain context.

This monorepo holds the work in progress. Two products are live, two
are paper-trading, one is a public studio site, the rest are
specs / experiments.

→ Studio site: **[ewwesolutions.work](https://ewwesolutions.work)**

---

## What's live

### [Faktomat](invoice_idp/) — Polish invoice OCR → JPK_FA(4)

[**faktomat.ewwesolutions.work**](https://faktomat.ewwesolutions.work)

Drop a Polish invoice PDF, get a JPK_FA(4) XML validated against the
Ministry of Finance schema, plus JSON and CSV. Pay-as-you-go,
0,50 PLN per invoice, no subscription. AWS Frankfurt; data stays in
the EU.

Stack: Python · FastAPI · Postgres · Anthropic SDK (Bedrock) ·
Stripe · arq worker · Cloudflare tunnel (cloudflared in-VM).

### [fiszkomat](fiszkomat/) — Polish-medical skrypt → Anki

[**fiszkomat.ewwesolutions.work**](https://fiszkomat.ewwesolutions.work)

Drop a long Polish farmakologia skrypt, get a generated Anki deck
sized for kolokwium use. Chunked Haiku generation aligned to
`Zajęcia N.` headers so attention doesn't degrade past page ten.
Optional Claude Opus 4.7 quality pass for fact-checking the deck.
Scanned PDFs handled via Claude vision fallback. Per-document
pricing, no subscription.

8 curated sample decks already on the landing — antybiotyki,
toksykologia, układ oddechowy, układ pokarmowy, leki przeciwwirusowe /
grzybicze / pasożytnicze, hormony, metabolizm wapnia + cukrzyca +
otyłość, plus a full Polish microbiology MCQ bank (Murray-aligned).
~1,160 cards.

Stack: Python · FastAPI · Anthropic SDK · genanki · Stripe · pypdf +
pdfplumber + pdftoppm for the OCR path.

---

## Paper-trading (educational only)

### [`momentum`](momentum/) — equities

Long-only monthly momentum on the S&P 500 (Jegadeesh-Titman 12-1).
Backtested walk-forward, running on Alpaca paper account daily,
Discord-wrapped.

### [`crypto_momentum`](crypto_momentum/) — crypto

Sibling of `momentum`. Weekly rebalance on top-N Alpaca-tradable
USD pairs by 30-day return. Separate paper account.

Both are **paper-only** by code property: `AlpacaClient` rejects
live mode without explicit `allow_live=True`, and there is no CLI
path to set that. Backtest results in each project's README are
historical and do not predict future returns. Educational use only.

---

## Specs / experiments

| Project | What it is |
|---|---|
| [`datafabric-hub`](datafabric-hub/) | Single-page mock of a data-fabric / API-hub dashboard. Vite · React 19 · TS · Tailwind. |

---

## What's gitignored

Standard secrets hygiene:

- `**/.env` — live API keys (Anthropic, Stripe, Postmark, AWS, etc.).
  Each project has a `.env.example` template.
- `*.pem`, `credentials.json`, `id_rsa` — keys.
- `invoice_idp/eval_set/` — real Polish invoices pulled from a
  personal inbox; third-party business PII.
- `**/state/*.json` — paper-trading account holdings / peak-equity
  state.
- Build caches: `__pycache__`, `.mypy_cache`, `node_modules`, `dist`.

Credential-leak prevention is delegated to GitHub Push Protection +
secret scanning on the remote.

---

## Running anything

Each project has its own setup. Quick pointers:

```bash
# Python projects
cd fiszkomat  # or invoice_idp, momentum, crypto_momentum
python -m venv .venv && source .venv/bin/activate
pip install -e .
# project-specific README has the rest

# datafabric-hub
cd datafabric-hub/app
npm install
npm run dev
```

---

## Contact

[kontakt@ewwesolutions.work](mailto:kontakt@ewwesolutions.work)
[ewwesolutions.work](https://ewwesolutions.work)
