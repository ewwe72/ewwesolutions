# Task: Polish invoice extraction

**Input**: a Polish invoice rendered as text (PDF->text typically via pymupdf
or pdfplumber upstream -- not in scope here, this task takes pre-extracted text).

**Output**: JSON conforming to `schema.json` -- JPK_FA-relevant fields:
seller/buyer NIPs and names, invoice number, dates, line items with VAT rates,
totals.

## Dataset (v0)

`cases/seed.jsonl` holds 10 hand-curated test cases. Sources:
- Synthetic, generated to cover the JPK_FA variation matrix: 23%/8%/5%/0% VAT,
  mixed-rate, foreign currency, missing optional fields, rounding edge cases,
  OCR-style artifacts.
- No real customer invoices (PII-free by construction).

## Dataset (v0.1 -- planned)

Will be replaced by `cases/real.jsonl` -- pulled from operator's email, run
through a classification step (gmail-pulled PDFs are "pies z buda" -- lots
of non-invoice noise), seeded by faktomat's Bedrock Claude Opus extraction,
then operator-verified field-by-field. See plan v0.1 sketch.

## Scoring

See `task.yaml` for weights and `../../runner/scoring.py` for implementations.
Composite score = weighted sum of:
- `field_accuracy` (0.7): per-field match across all cases (averaged). Numeric
  tolerance +-0.01. List-of-objects (line_items) matched order-insensitively.
- `schema_validity` (0.2): % of outputs that parse + conform to schema.
- `latency_p50` (0.05): provider-reported latency, normalised.
- `cost_per_1k` (0.05): from `cost_per_1m_in/out` in `task.yaml`.

## Adding a case

Append a JSON object to `cases/seed.jsonl` with:
- `id`: unique string (`case-NNN`)
- `input`: the invoice text (as one JSON string with `\n` newlines)
- `expected_output`: ground truth conforming to `schema.json`
