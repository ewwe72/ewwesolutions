# Invoice IDP for Polish Accounting — V1 Specification

**Document version:** 1.3
**Date authored:** 2026-05-12
**Status:** Draft — pending customer-research validation before implementation begins
**Document scope:** Phases 1.0 / 1.1 / 1.2 (V1 MVP through differentiated Polish-ERP export)

**Revision history:**
- **1.3 (2026-05-13)** — Cost-budget pivot. Seed budget $10 Claude
  + AWS free tier. §6 drops Sonnet auto-fallback — Haiku-only routing,
  review-page edits are the correction layer; Sonnet reserved for the
  manual "Re-ekstrakcja" button. §11 swaps Hetzner CPX21 → AWS EC2
  t2.micro running the same docker-compose stack; bootstrap principle,
  reinvest revenue into bigger infra. §14 reframes pricing — beta runs
  paid-only (0.50 PLN/invoice, $5 min top-up, phone-verified), 3-
  lifetime-uploads free tier moves to V1.4. §10 phone verification
  moves earlier (mandatory before any upload), no longer a gate at 30.
- **1.2 (2026-05-12)** — Phase 1 retrospective. §6 routing relaxed
  (drop hard-warning trigger; Phase 1 eval showed it pulled Haiku hit
  rate to ~44%, below §14 60% gate — confidence penalty mechanic
  + 0.80 threshold already handle multi-warning cases). §16 adds
  known limitations: VAT-inclusive-product receipts (GanjaFarmer
  pattern, products at brutto=netto with VAT only on shipping) and
  OFFER/quote → PROFORMA mapping (no separate `OFFER` enum in V1).
  §15 Phase 1 done-when clarifies that the accuracy gate is measured
  on in-scope documents only (out-of-scope docs moved to `_noise/`
  by curate are excluded). Phase 1 closed; eval set has 195 in-scope
  faktury after cleanup, 17/18 spotchecked, 11/13 "good" on both
  header and totals (84.6% — just below 85%/90% gate, with the
  shortfall driven by two documented schema-gap limitations).
- **1.1 (2026-05-12)** — Parameter pass before build start. Free tier
  50 → 25 invoices; Pydantic v2 idiomatic `Annotated[Decimal, Field(...)]`
  in the data model; `extracted_at` promoted from `date` to `datetime`;
  corrected Haiku/Sonnet cost ratio (~10× → ~3×); added margin-
  sensitivity table tied to Haiku hit rate (§14); raised Phase 0 / 1
  invoice sample from 20 to 50 and added cross-template coverage
  requirement; added Haiku-hit-rate measurement gate in Phase 1; added
  XSD-is-source-of-truth disclaimer to §7.1 JPK_FA example;
  resolved open question 8 (Anthropic API access path) as a decision;
  enumerated env vars by phase in §0.
- **1.0 (2026-05-12)** — Initial draft.

---


## 1. Executive summary

**Product:** AI-powered invoice data extraction SaaS, Polish-market-focused,
monetised as freemium subscription with per-invoice metered overage.

**Core value proposition:** Solo-developer-bootstrappable competitor to
Parseur and Saldeo Smart, differentiated by (a) native generation of
Polish regulatory formats — JPK_FA, KSeF FA(2) — that international
players don't support and (b) direct file-format integration with
Polish accounting systems (Subiekt EDI++ in V1.2, Comarch/Symfonia
post-V1) that Parseur outsources to Zapier.

**V1 scope:** End-to-end pipeline from PDF upload through structured-data
extraction to multi-format export. Phases 1.0 / 1.1 / 1.2 deliver:

- **V1.0:** Web upload, Claude-vision extraction, JPK_FA(4) XML export,
  JSON API, paid tier with Stripe.
- **V1.1:** CSV/XLSX export with configurable column mappings.
- **V1.2:** Insert Subiekt EDI++ (.epp) export, hot-folder / email-forwarding
  ingestion.

**Hard precondition:** customer-research validation (≥3 of 5
interviewed accountants express explicit pain that this product solves)
before V1.0 implementation begins. **No code beyond the extraction
prototype is written until that validation completes.**

---

## 2. Product positioning

### Target customer (V1)

**Primary:** *biura rachunkowe* (accounting offices) with 1-10 employees
serving 50-300 SME clients. They process 500-10,000 invoices/month
across all clients, mix of Polish faktury VAT / foreign invoices /
paragony. They have at least one accounting system (most likely
Subiekt GT, iFirma, or Comarch Optima). They have used Saldeo Smart or
considered it; their pain points are price (>500 PLN/month feels
excessive for their volume) or UX clunkiness.

**Secondary:** solopreneurs (jednoosobowa działalność, sp. z o.o.
without dedicated księgowy) who do their own bookkeeping and need to
extract data from supplier invoices for VAT reporting. Smaller market,
smaller ARPU, but lower acquisition friction.

**Not the target (V1):** large corporations, enterprise IT departments,
sektor publiczny. Those want SOC 2, dedicated SLAs, on-prem options —
different product, different sales motion.

### Competitive positioning

| Property | Parseur | Saldeo Smart | This product |
|---|---|---|---|
| Polish JPK_FA export | No | Yes | **Yes (native)** |
| KSeF FA(2) export | No | Yes | **Yes (native)** |
| Subiekt EDI++ direct | No (Zapier) | Yes (deep integration) | **Yes (file export, V1.2)** |
| Comarch Optima direct | No | Yes | Post-V1 |
| Modern AI extraction | Yes | Mixed | **Yes** |
| Self-serve / no sales contact | Yes | No | **Yes** |
| Polish-localised UI | Partial | Yes | **Yes** |
| Free tier | Yes (20 docs/mo) | No | **Yes (3 docs lifetime, V1.4+ — SMS-verified)** |
| Starting paid tier | ~$39/mo | ~300 PLN/mo | **0.50 PLN/invoice (V1.3 beta) → 79 PLN/mo (V1.4+)** |

### Pricing tiers

**V1.3 beta (current):** paid-only, pay-as-you-go.

| Tier | Top-up min | Per-invoice | Output formats | API |
|---|---:|---:|---|---|
| Beta PAYG | $5 | 0.50 PLN (~$0.13) | JPK_FA, JSON | No |

**V1.4+ (post-beta, once revenue covers Bedrock costs):**

| Tier | Monthly price | Invoices included | Output formats | API |
|---|---:|---:|---|---|
| Free | 0 PLN | 3 lifetime (SMS-verified) | JPK_FA, JSON | No |
| Starter | 79 PLN | 500 | + CSV/XLSX | No |
| Pro | 199 PLN | 2 000 | + EDI++, hot-folder | Yes |
| Business | 599 PLN | 10 000 | All + priority | Yes |
| Biuro | custom | 10 000+ | All + multi-client workspace | Yes |

Overage: 0.10 PLN per invoice above plan, billed monthly. No annual
prepay discount in V1 (keeps revenue recognition simple).

**Payment processing:** Stripe (cards + SEPA) primary, Przelewy24
secondary for PLN customers who prefer local payment rails. Both via
Stripe's Przelewy24 connector — no separate integration.

---

## 3. Scope by phase

### V1.0 — MVP

**In scope:**

- Email/password authentication, email verification, password reset
- Single-tenant accounts (one user = one organisation; multi-user later)
- Web UI: login, dashboard, upload, review, export, settings, billing
- Single-PDF upload (drag-drop + file picker)
- Multi-page invoice support (up to 10 pages per PDF)
- Claude vision extraction → canonical Invoice schema
- Field-level confidence scores
- Manual correction UI (edit any extracted field before export)
- JPK_FA(4) XML export with XSD validation
- JSON output via download
- Stripe billing for Starter tier (Free tier needs no billing)
- Discord ops alerting (errors, signups, payments)
- EU hosting, RODO-compliant data flow

**Explicitly out:**

- KSeF API submission (generate FA(2) XML in V2; submission is
  operator-side because each Polish business has to authenticate to
  KSeF separately)
- OCR fallback for poor scans
- Foreign-language invoices beyond Polish
- Mobile apps
- Multi-user / team features
- Webhook/API ingestion (V1.2 adds hot-folder; full API ingestion in V2)
- Custom field extraction beyond the canonical schema

### V1.1 — CSV/XLSX export

**Adds:**

- CSV export with two layouts: "one row per invoice" and "one row per
  line item"
- XLSX export with multi-sheet workbook (Invoices / LineItems / VATSummary)
- Configurable column mappings — user picks which canonical fields go
  to which output columns, saves named templates
- Bulk download (zip of N invoices in chosen format)
- Pre-built templates for: Subiekt GT import, Comarch Optima import,
  iFirma import, generic

### V1.2 — Insert Subiekt EDI++ + ingestion expansion

**Adds:**

- Insert EDI++ (.epp) file export, validated against Subiekt GT
  import spec
- Bulk EDI++ generation (one file per N invoices)
- Hot-folder ingestion: per-user SFTP credentials, files dropped get
  processed
- Email-forwarding ingestion: per-user inbox address (e.g.
  `u-12345@in.invoiceidp.pl`); PDF attachments and PDFs linked in body
  are processed; reply email returned with results
- Basic usage dashboard: invoices processed, success rate, average
  confidence, cost (for paid tiers)

---

## 4. Technical architecture

```
┌────────────────────────────────────────────────────────────────┐
│                       INGESTION                                │
│  Web upload  │  SFTP hot folder (V1.2)  │  Email (V1.2)        │
└───────────────────────────┬────────────────────────────────────┘
                            │ (PDF blob)
                            ▼
┌────────────────────────────────────────────────────────────────┐
│                    PRE-PROCESSING                              │
│  PDF → PNG/JPEG pages (PyMuPDF)                                │
│  Page count limit, file size limit, MIME validation            │
└───────────────────────────┬────────────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────────────┐
│                    EXTRACTION                                  │
│  Claude Sonnet 4.6 vision (AWS Bedrock eu-central-1)           │
│  Structured tool-use output → CanonicalInvoice                 │
│  Per-field confidence (model self-assessed + heuristics)       │
└───────────────────────────┬────────────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────────────┐
│                    VALIDATION                                  │
│  NIP checksum, REGON checksum                                  │
│  VAT math (net + VAT = gross, per line and per rate group)     │
│  Date logic (issue ≤ due, sale ≤ issue+30d typical)            │
│  Currency normalisation                                        │
└───────────────────────────┬────────────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────────────┐
│                    PERSISTENCE                                 │
│  CanonicalInvoice → Postgres                                   │
│  Original PDF → AWS S3 (eu-central-1, S3 server-side encrypt) │
│  Audit log entry                                               │
└───────────────────────────┬────────────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────────────┐
│                    REVIEW + CORRECTION                         │
│  Web UI: PDF preview ↔ field-by-field edit                     │
│  Confidence display, validation warnings inline                │
│  Save edits back to CanonicalInvoice                           │
└───────────────────────────┬────────────────────────────────────┘
                            ▼
┌────────────────────────────────────────────────────────────────┐
│                    EXPORT                                      │
│  JPK_FA(4) XML │ CSV │ XLSX │ EDI++ │ JSON │ (KSeF FA(2) V2)   │
│  Validation per format before delivery                         │
│  Download / SFTP push / email return                           │
└────────────────────────────────────────────────────────────────┘
```

### Tech stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | Consistency with operator's existing bots, `mypy --strict` shop |
| Backend framework | FastAPI | Type-hinted, auto OpenAPI, async-native, fast |
| Schema/validation | Pydantic v2 | Already in FastAPI, runtime + static validation |
| ORM | SQLAlchemy 2.x | Mature, async support, good with Pydantic |
| Migrations | Alembic | Standard SQLAlchemy companion |
| Database | PostgreSQL 15 | Production-grade, JSON support for flexible fields |
| Frontend | HTMX + Jinja2 + Tailwind | Solo-dev velocity, no JS build pipeline, no SPA maintenance. Re-evaluate post-V1 if interactivity demands React. |
| LLM | Claude Haiku 4.5 via AWS Bedrock (eu-central-1); Sonnet 4.6 as manual re-extract only | Haiku-only routing (v1.3 pivot) ≈$0.004/invoice; review-page edits cover the accuracy gap |
| PDF processing | PyMuPDF (`fitz`) | Reliable, fast, MIT licence |
| File storage | AWS S3 (eu-central-1) | EU resident, free tier 5 GB + 20k GET / 2k PUT per month |
| Payments | Stripe + Przelewy24 (via Stripe) | Card + local Polish rails through one integration |
| Email | Postmark (transactional) + SES (bulk) | EU regions available |
| Logging | Structured JSON → Better Stack or self-hosted Loki | Same pattern as trading bots |
| Monitoring | UptimeRobot + Sentry | Free tiers cover MVP |
| Hosting | AWS EC2 t2.micro (1 GB RAM, 1 vCPU, free tier Y1; ~$10/mo Y2+) | Single-box docker-compose: app + worker + postgres + redis + caddy |
| CI/CD | GitHub Actions → Docker → EC2 SSH | Standard, no surprises |
| Testing | pytest + Playwright (e2e) | Same testing discipline as trading bots |
| Type checking | `mypy --strict` | Same as trading bots |

### Repository layout

```
invoice_idp/
├── pyproject.toml
├── mypy.ini
├── README.md
├── SPEC.md                        # this document
├── CHANGELOG.md                   # one-line entry per substantive change
├── docker-compose.yml
├── .env.example                   # template (do NOT commit real .env)
├── alembic/
├── src/
│   ├── app/
│   │   ├── main.py                # FastAPI entrypoint
│   │   ├── config.py
│   │   ├── deps.py                # Dependency injection
│   │   ├── auth/                  # Login, signup, sessions, password reset
│   │   ├── billing/               # Stripe webhooks, usage metering
│   │   ├── api/                   # REST endpoints
│   │   ├── web/                   # HTMX views, Jinja templates
│   │   └── workers/               # Background jobs (extraction queue)
│   ├── pipeline/
│   │   ├── ingestion/             # Web, SFTP (V1.2), Email (V1.2)
│   │   ├── extraction/            # Claude vision wrapper, prompts
│   │   ├── validation/            # NIP/REGON/VAT/date checks
│   │   ├── persistence/           # DB writes, S3 writes
│   │   └── export/
│   │       ├── jpk_fa.py          # V1.0
│   │       ├── csv_xlsx.py        # V1.1
│   │       ├── edi_pp.py          # V1.2
│   │       └── json_export.py     # V1.0
│   ├── models/
│   │   ├── invoice.py             # CanonicalInvoice + sub-models
│   │   ├── user.py
│   │   ├── org.py
│   │   ├── usage.py
│   │   └── audit.py
│   └── utils/
│       ├── nip.py                 # NIP checksum
│       ├── regon.py
│       └── polish_dates.py
├── scripts/
│   ├── extract_prototype.py       # Standalone Claude vision prototype
│   └── ops_alerter.py             # Discord wrapper for ops alerts
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── prompts/
│   └── extraction_v1.md           # Versioned prompts for the LLM
└── schemas/
    ├── jpk_fa_v4.xsd              # Downloaded from Ministerstwo Finansów
    └── edi_pp_reference.md        # Insert EDI++ format reference
```

---

## 5. Data model — canonical Invoice schema

All output formats serialise from one Pydantic model. This is the
contract between the extraction layer and the export layer.

```python
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated
from pydantic import BaseModel, Field


class InvoiceType(str, Enum):
    REGULAR = "VAT"              # Faktura VAT
    PRO_FORMA = "PROFORMA"
    CORRECTION = "KOREKTA"       # Faktura korygująca
    DUPLICATE = "DUPLIKAT"
    SIMPLIFIED = "UPROSZCZONA"   # Paragon z NIP
    RECEIPT = "PARAGON"          # Plain paragon (no NIP)


class Currency(str, Enum):
    PLN = "PLN"
    EUR = "EUR"
    USD = "USD"
    GBP = "GBP"
    CHF = "CHF"
    CZK = "CZK"


class VATRate(str, Enum):
    """Polish VAT rate codes — must match JPK_FA enum values."""
    R23 = "23"
    R8 = "8"
    R5 = "5"
    R0 = "0"
    ZW = "zw"     # zwolnione (exempt)
    NP = "np"     # nie podlega (not taxed)
    OO = "oo"     # odwrotne obciążenie (reverse charge)


class Money(BaseModel):
    """Currency-aware decimal amount. All amounts in invoice are this type."""
    amount: Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
    currency: Currency


class Counterparty(BaseModel):
    """Seller or buyer."""
    name: str
    nip: str | None = None              # Validated checksum if present
    regon: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str = "PL"                  # ISO 3166-1 alpha-2
    bank_account: str | None = None      # IBAN format
    confidence: dict[str, float] = Field(default_factory=dict)


class LineItem(BaseModel):
    line_no: int
    description: str
    quantity: Annotated[Decimal, Field(max_digits=12, decimal_places=4)]
    unit: str = "szt."                   # szt., kg, godz., etc.
    unit_price_net: Money
    vat_rate: VATRate
    discount_pct: Annotated[Decimal, Field(max_digits=5, decimal_places=2)] = Decimal(0)
    net_value: Money
    vat_value: Money
    gross_value: Money
    confidence: dict[str, float] = Field(default_factory=dict)


class VATSummaryEntry(BaseModel):
    """One row per VAT rate in the invoice's VAT summary table."""
    rate: VATRate
    net_total: Money
    vat_total: Money
    gross_total: Money


class PaymentInfo(BaseModel):
    method: str | None = None           # "przelew", "gotówka", "karta"
    due_date: date | None = None
    paid: bool = False
    paid_date: date | None = None
    bank_account: str | None = None     # IBAN


class CanonicalInvoice(BaseModel):
    """The single source of truth, populated by extraction, consumed by exports."""

    # Identification
    invoice_number: str
    invoice_type: InvoiceType = InvoiceType.REGULAR
    issue_date: date
    sale_date: date | None = None        # Data sprzedaży / wykonania usługi
    place_of_issue: str | None = None

    # Parties
    seller: Counterparty
    buyer: Counterparty

    # Lines + summary
    lines: list[LineItem]
    vat_summary: list[VATSummaryEntry]

    # Totals (must reconcile against vat_summary)
    total_net: Money
    total_vat: Money
    total_gross: Money

    # Payment
    payment: PaymentInfo

    # Free-form
    notes: str | None = None

    # Extraction metadata (not serialised to JPK_FA / EDI++ etc.)
    overall_confidence: float                    # 0.0 - 1.0
    extraction_warnings: list[str] = []          # validation issues
    source_pdf_id: str                           # ref to S3 object
    extracted_at: datetime                       # UTC timestamp; audit log needs sub-day precision
    extracted_model: str                         # e.g. "claude-sonnet-4-6"
    extraction_version: str                      # prompt version
```

### Validation rules

Run during extraction; populate `extraction_warnings`. Soft warnings
(don't block export) vs hard errors (require user review).

| Rule | Severity | Description |
|---|---|---|
| NIP format | Hard | 10 digits, valid checksum (multiply digits by weights `[6,5,7,2,3,4,5,6,7]`, mod 11) |
| REGON format | Hard | 9 or 14 digits, valid checksum |
| Line VAT math | Hard | `abs(net × (vat_rate / 100) - vat_value) ≤ 0.02 PLN` per line |
| Invoice totals | Hard | `sum(lines.net) == total_net` (±0.02), same for VAT, gross |
| VAT summary consistency | Hard | `sum(vat_summary.net by rate) == aggregated lines.net by rate` |
| Issue ≤ due | Soft | Warn if due_date < issue_date |
| Currency uniformity | Hard | All Money values use the same Currency |
| Required fields | Hard | invoice_number, issue_date, seller.name, total_gross |
| Seller NIP for FV | Hard | If type=REGULAR or CORRECTION, seller.nip is required |

---

## 6. Extraction pipeline

### Model selection

**Default:** `claude-haiku-4-5` via AWS Bedrock `eu-central-1` (Frankfurt).
- Cost: ~$1 per 1M input tokens, ~$5 per 1M output tokens
- Image input: ~$0.0003 per image at 1024×1024 resolution
- Per invoice (avg 1.5 pages, ~2K output tokens): **~$0.004**

**Fallback (manual only):** `claude-sonnet-4-6` — invoked by the operator
via the "Re-ekstrakcja (Sonnet)" button on the review page when Haiku
output isn't worth correcting by hand.

**Routing logic (v1.3):**

1. Every upload runs Haiku 4.5 once. The result is persisted with its
   self-reported confidence and any hard/soft validation warnings.
2. The Phase 4 editable review page is the correction layer. Operators
   fix Haiku errors by typing the right values; the per-field
   `confidence` pills surface what to double-check (green ≥0.90,
   yellow ≥0.70, red <0.70).
3. If the operator decides correction-by-hand isn't worth it, they
   click "Re-ekstrakcja (Sonnet)". The system runs Sonnet only (no
   Haiku re-run), persists the new result, and clears any prior
   `user_reviewed_at` review stamp.

**Why no auto-fallback:** the v1.2 spec ran Haiku → auto-Sonnet at
confidence < 0.80. Phase 1 eval showed 44% Haiku-only hit rate at that
threshold, so 56% of invoices doubled the LLM cost. With the editable
review page (Phase 4 chunk 3b) now shipped, operator correction is
cheaper than Sonnet's $0.012/invoice for ~90% of low-confidence cases.
Sonnet stays available but on demand only, not by default. Net cost
reduction: ~6× ($0.011–0.025 → $0.004 per invoice average).

### Prompt design

Versioned files in `prompts/extraction_v1.md`. Outputs structured JSON
via Anthropic tool use (`input_schema` matching `CanonicalInvoice`).
Prompt instructs:

- Reply in JSON only, never prose
- Polish field names in the source PDF mapped to canonical English schema names
- Confidence scores per field (model self-assessment on 0-1 scale)
- Currency detection from explicit symbols / codes in the document
- VAT rate detection from explicit `23%`, `8%`, `zw`, `np` markers in the line table
- Multi-page invoices: aggregate line items across pages, headers from
  page 1, totals from last page

**Prompt versioning matters.** Every prompt change increments
`extraction_version`. Re-extractions are tagged with the version used.
Prompt revisions can be A/B-tested on held-out invoices without
breaking historical data.

### Pre-processing

```python
def pdf_to_images(pdf_bytes: bytes, max_pages: int = 10) -> list[bytes]:
    """Convert PDF pages to PNG bytes. Each page rasterised at 200 DPI,
    downscaled to ≤1024×1448 (A4 ratio) to fit Claude's image size budget."""
```

- Max 10 pages per invoice. Above that: error, require user to split.
- Max 20 MB PDF input.
- MIME validation before opening (must be `application/pdf`).
- Pages with no text *and* no high-contrast pixels (likely blank):
  skipped silently.

### Confidence scoring

Two layers:

1. **Model self-assessed** — Claude is asked to emit a confidence per
   field. Useful but unreliable in absolute terms.
2. **Validation-derived** — `extraction_warnings` populated by the
   validation layer reduce overall_confidence proportionally.

`overall_confidence` formula:

```
base = mean(field_confidences)
penalty = 0.10 per hard validation warning, 0.03 per soft
overall = max(0.0, base - penalty)
```

UI shows `overall_confidence` as a coloured pill:

- ≥ 0.90: green ("Ready to export")
- 0.70–0.90: yellow ("Review recommended")
- < 0.70: red ("Manual correction required before export")

---

## 7. Output formats — detailed

### 7.1 JPK_FA(4) XML (V1.0)

**Schema source:** `https://www.podatki.gov.pl/jpk/Schemat_JPK_FA(4)_v1-0_2022.xsd`
— download once, commit to `schemas/jpk_fa_v4.xsd`, validation via `lxml`.

**The XSD is the source of truth.** The XML structure below is
illustrative only; the field-by-field meaning of `P_3A`..`P_5B`
(which side is buyer vs seller, country code vs NIP, etc.) and the
identity of `Podmiot1` (the JPK filer — for outbound sales-side
JPK_FA this is the seller) must be re-derived from the XSD and the
Ministerstwo Finansów documentation at implementation time, not
copied from this draft. The example contains placeholder values that
should not be relied on. Unit tests in Phase 5 must assert the
generator's output validates against the committed XSD and matches
field semantics confirmed from the official documentation.

**Structure (illustrative — verify field semantics against XSD):**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<JPK xmlns="http://crd.gov.pl/wzor/2022/03/03/11455/">
  <Naglowek>
    <KodFormularza kodSystemowy="JPK_FA (4)" wersjaSchemy="1-0">JPK_FA</KodFormularza>
    <WariantFormularza>4</WariantFormularza>
    <DataWytworzeniaJPK>2026-05-12T15:35:00</DataWytworzeniaJPK>
    <DataOd>2026-05-01</DataOd>
    <DataDo>2026-05-31</DataDo>
    <NazwaSystemu>InvoiceIDP/1.0</NazwaSystemu>
    <CelZlozenia>1</CelZlozenia>
    <KodUrzedu>0202</KodUrzedu>           <!-- From customer settings -->
    <KodWaluty>PLN</KodWaluty>
  </Naglowek>
  <Podmiot1>
    <IdentyfikatorPodmiotu>
      <NIP>1234567890</NIP>
      <PelnaNazwa>...</PelnaNazwa>
    </IdentyfikatorPodmiotu>
  </Podmiot1>
  <Faktura typ="G">
    <KodWaluty>PLN</KodWaluty>
    <P_1>2026-05-15</P_1>                  <!-- issue date -->
    <P_2A>FV/05/2026/001</P_2A>            <!-- invoice number -->
    <P_3A>Nazwa Nabywcy sp. z o.o.</P_3A>
    <P_3B>ul. Przykładowa 1, 00-001 Warszawa</P_3B>
    <P_3C>Nazwa Sprzedawcy</P_3C>
    <P_3D>ul. Inna 2, 00-002 Warszawa</P_3D>
    <P_4A>PL</P_4A>
    <P_4B>9876543210</P_4B>                <!-- seller NIP -->
    <P_5A>PL</P_5A>
    <P_5B>1234567890</P_5B>                <!-- buyer NIP -->
    <P_6>2026-05-15</P_6>                  <!-- sale date -->
    <P_13_1>100.00</P_13_1>                <!-- net total at 23% -->
    <P_14_1>23.00</P_14_1>                 <!-- VAT total at 23% -->
    <P_15>123.00</P_15>                    <!-- gross total -->
    <RodzajFaktury>VAT</RodzajFaktury>
  </Faktura>
  <FakturaCtrl>
    <LiczbaFaktur>1</LiczbaFaktur>
    <WartoscFaktur>123.00</WartoscFaktur>
  </FakturaCtrl>
  <FakturaWiersz typ="G">
    <P_2B>FV/05/2026/001</P_2B>
    <P_7>Usługa konsultingowa</P_7>
    <P_8A>godz.</P_8A>
    <P_8B>10.0000</P_8B>
    <P_9A>10.00</P_9A>
    <P_11>100.00</P_11>
    <P_12>23</P_12>
  </FakturaWiersz>
  <FakturaWierszCtrl>
    <LiczbaWierszyFaktur>1</LiczbaWierszyFaktur>
    <WartoscWierszyFaktur>100.00</WartoscWierszyFaktur>
  </FakturaWierszCtrl>
</JPK>
```

**Implementation:** `lxml.etree.Element` builders. Validation against
XSD before return. Errors surfaced to user with explicit field-name
pointer (e.g. "Line 3 unit must be ≤ 50 chars per JPK_FA schema").

**`KodUrzedu` (tax office code):** taken from user settings.
User configures their tax office once on signup.

**`CelZlozenia`:** defaults to 1 (złożenie); user can select 2 (korekta)
explicitly at export time.

### 7.2 CSV / XLSX (V1.1)

**Two layout modes, user-selectable:**

**Mode A — "Wide" (one row per invoice, lines flattened):**

```
invoice_number,issue_date,seller_name,seller_nip,buyer_name,buyer_nip,
total_net,total_vat,total_gross,currency,line_count,first_line_desc
```

**Mode B — "Long" (one row per line item, invoice fields repeated):**

```
invoice_number,issue_date,seller_name,seller_nip,buyer_name,buyer_nip,
line_no,description,quantity,unit,unit_price_net,vat_rate,
net_value,vat_value,gross_value,currency
```

**XLSX:** three-sheet workbook

- Sheet 1 "Faktury": one row per invoice (Mode A)
- Sheet 2 "Pozycje": one row per line item (Mode B)
- Sheet 3 "Podsumowanie VAT": one row per (invoice, VAT rate) summary

**Configurable column mappings:**

- User picks which canonical fields appear, in what order, with what
  header names
- Templates saved per-user, named
- Pre-shipped templates: `subiekt_gt_import`, `comarch_optima_import`,
  `ifirma_csv`, `generic`

**Implementation:** `pandas` for CSV, `openpyxl` for XLSX. Column-map
serialised as JSON in user's settings table.

### 7.3 Insert EDI++ / .epp (V1.2)

**Reference:** Insert publishes the EDI++ specification at
`https://www.insert.com.pl/dla-programistow/dokumenty/format-edi-pp.pdf`
(verify URL at implementation time; may have moved). Document covers
Subiekt GT, Subiekt Nexo, Rachmistrz, Rewizor.

**Format characteristics:**

- Plain-text file, Windows-1250 encoding
- Section headers in square brackets: `[NAGLOWEK]`, `[ZAWARTOSC]`,
  `[KONIEC]`
- Pipe-delimited fields within sections
- Decimal separator: comma (Polish locale)
- Date format: `YYYYMMDD`

**Example structure (illustrative):**

```
[INFO]
1,0|1250|PLZL|2026-05-12 15:35:00|Nazwa firmy|...|InvoiceIDP/1.0
[NAGLOWEK]
faktura|FV/05/2026/001|FV|2026-05-15|2026-05-15|...
[ZAWARTOSC]
Usługa konsultingowa|szt.|10,0000|10,00|23|100,00|23,00|123,00
[STOPKA]
100,00|23,00|123,00
[KONIEC]
```

**Implementation:** string generation with explicit encoding. No SDK
dependency. Validation: round-trip parse via own EDI++ parser to catch
malformed output before delivery.

**Testing strategy:** Insert provides a free demo of Subiekt GT for
developers. Each EDI++ release is tested by:

1. Generating from canonical invoice
2. Importing into Subiekt GT demo
3. Asserting invoice appears in Subiekt's faktury list with correct
   totals
4. Manual for V1.2; consider Playwright-on-Windows-VM automation post-V1

**Edge cases to handle:**

- Encoding: any non-Windows-1250 chars must be transliterated or
  stripped (e.g. emoji in description)
- VAT rate "zw" / "np" — EDI++ uses different codes than JPK_FA;
  mapping table required
- Foreign currency invoices: EDI++ supports `PLZL` and `WALUTA` modes;
  non-PLN goes through `WALUTA` mode with conversion rate per line

---

## 8. REST API

OpenAPI auto-generated from FastAPI. All endpoints under `/api/v1/`.
Auth via API key in `Authorization: Bearer ak_...` header (separate
from web session cookies).

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/invoices` | Upload PDF, returns job id |
| `GET` | `/invoices` | List user's invoices (paginated, filter by status / date) |
| `GET` | `/invoices/{id}` | Get extracted data + confidence + warnings |
| `PATCH` | `/invoices/{id}` | Submit field corrections |
| `DELETE` | `/invoices/{id}` | Soft-delete (RODO right to erasure) |
| `POST` | `/invoices/{id}/exports` | Request export: `jpk_fa` / `csv` / `xlsx` / `edi_pp` / `json` |
| `GET` | `/exports/{id}` | Download export file (presigned S3 URL or direct stream) |
| `GET` | `/usage` | Current month's usage (invoices processed, cost, plan limit) |
| `GET` | `/health` | Liveness check (no auth) |

**Rate limits:** 10 req/sec per API key (burst 30), 1000 invoices/hour
per account (above this triggers async batch mode).

**Webhooks (V1.2):** `POST /webhooks` to register a URL. Events:
`invoice.extracted`, `invoice.failed`, `export.ready`. Signed with
HMAC-SHA256 per event.

---

## 9. Web UI

HTMX + Jinja2 + Tailwind. No SPA. Pages render server-side, interactive
bits (file upload, field editing, polling) use HTMX `hx-*` attributes.

### Page list (V1.0)

1. **`/`** — landing page (marketing, anonymous)
2. **`/signup`, `/login`, `/forgot-password`** — auth flows
3. **`/app`** — dashboard (auth required): recent invoices, monthly
   usage, plan status
4. **`/app/wgraj`** — drag-drop + file picker, shows progress and queue
5. **`/app/faktury`** — paginated list, search by number / seller NIP / date
6. **`/app/faktury/{id}`** — review page: PDF preview left, editable
   fields right, "Export as…" buttons. Sub-routes:
   - `POST /app/faktury/{id}/popraw` — submit operator corrections
   - `POST /app/faktury/{id}/ponow-ekstrakcje` — re-extract with Sonnet
   - `GET /app/faktury/{id}/pdf` — server-side PDF proxy from S3
   - `GET /app/faktury/{id}/eksport/{json,csv,jpk_fa}` — download
7. **`/app/eksporty`** — list of generated export files (V1.x; current
   beta just downloads directly from the review page)
8. **`/app/ustawienia`** — org info (NIP, REGON, KodUrzedu), default
   templates, API key management
9. **`/app/rozliczenia`** — plan, faktury, metoda płatności
   (Stripe-hosted billing portal)

Naming convention: user-facing app routes use Polish slugs (this is a
Polish-first product; URLs are part of the UX). Tech routes stay
English: `/api/v1/*`, `/auth/*`, `/health`, `/webhooks/*`.

### Review page UX (the most important screen)

Left panel: PDF rendered with PDF.js, sticky.
Right panel: scrollable form with sections (Header / Seller / Buyer /
Lines / Totals / Payment).

Each field shows:

- Current extracted value (editable)
- Confidence pill (green/yellow/red)
- Validation warning inline if present

Buttons:

- "Save corrections" — patches canonical invoice, marks as user-reviewed
- "Re-extract" — re-runs Sonnet 4.6 (counts against monthly quota)
- "Export as JPK_FA / CSV / XLSX / EDI++ / JSON" — generates and downloads

**Keyboard shortcuts** (biuro accountants live on keyboard):

- `Tab` / `Shift+Tab` across fields
- `Ctrl+S` saves
- `Ctrl+E` opens export menu
- `?` shows shortcut help

---

## 10. Auth & billing

### Auth

- Email + password (argon2id hashing)
- Email verification mandatory before processing invoices
- Password reset via emailed token (15-min expiry)
- Session cookie (httponly, secure, samesite=lax), 30-day expiry
- API keys for programmatic access, generated in settings, scoped to
  org, revocable
- No OAuth / SSO in V1

### Billing (Stripe)

- Stripe Checkout for plan changes
- Stripe Customer Portal for payment method management
- Webhooks: `customer.subscription.updated`,
  `customer.subscription.deleted`, `invoice.payment_failed` — handled
  in `src/app/billing/webhooks.py`
- Usage metering: every successful extraction increments a counter;
  counter compared to plan limit on each upload; overage tracked
  separately for end-of-month invoicing
- VAT handling: Stripe Tax for EU VAT compliance (Polish 23% VAT applied
  to Polish customers; reverse charge for EU B2B with valid NIP-EU)

### Free tier abuse prevention (v1.3 — phone gate moves upfront)

- One free account per verified email
- Email domain blocklist for disposable mail providers
- IP rate limit on signup (10 accounts / 24h / IP) — Phase 6
- **Phone verification mandatory before any upload** (was: gated at
  30 invoices). Twilio Verify ~$0.05/verification is cheap insurance
  vs Bedrock cost of unmetered abuse. Phase 6 deliverable; not yet
  implemented.

---

## 11. Hosting & infrastructure

### V1 launch — AWS free tier (Year 1)

V1.3 pivot: hosting moves from Hetzner Cloud CPX21 to a single AWS EC2
t2.micro running the same docker-compose stack. The app is provider-
agnostic (storage is S3-compatible end-to-end); the only thing that
changes is the bill. **Bootstrap principle:** free tier covers ~12 months
at $0 for everything except Bedrock. Reinvest customer payments into
larger instances (CPX21-equivalent, RDS managed Postgres, CloudFront)
as soon as cash flow supports it — the architecture below is the seed,
not the ceiling.

| Component | Provider | Spec | Year 1 Cost/mo |
|---|---|---|---:|
| App + worker + Postgres + Redis | AWS EC2 t2.micro | 1 GB RAM, 1 vCPU, 8 GB EBS, 750 hr/mo free | **$0** |
| Object storage | AWS S3 `eu-central-1` (Frankfurt) | 5 GB free + 20k GET / 2k PUT / mo | **$0** |
| Bedrock | AWS `eu-central-1` | Haiku-only default; pay-per-token | ~$2-30 |
| Email | Postmark | 100 emails/day free | **$0** |
| Domain + DNS | Cloudflare | 1 domain, DNS free | €10/year |
| Monitoring | UptimeRobot free + Sentry team plan | | $0-26 |
| **Total** | | | **~$2-60/mo (Y1)** |

**Year 2+ (free tier expires):** EC2 t2.micro ~$10/mo, S3 ~$0.50/mo,
Bedrock unchanged. Total ~$15-50/mo — comparable to original Hetzner
€60-150 plan but at half the floor cost thanks to Haiku-only routing.

**Bedrock budget guardrails:** the seed Bedrock budget is $10 (~2500
Haiku-only invoices at $0.004 each). Reinvest revenue from paying
customers directly into raising the cap as it depletes — at 0.50 PLN
charged per invoice (~$0.13), the first ~80 paid invoices cover the
full $10 seed. Add structured logging of `ExtractionRun.total_input_tokens`
+ `total_output_tokens` per call (Phase 6); alert when cumulative
estimated spend crosses 70% of the current cap so reinvestment timing
isn't reactive.

### Scale-up triggers (post-V1, when load demands)

- Move Postgres to dedicated managed DB (Hetzner Managed DB or Supabase EU)
  when local DB exceeds 50% of VPS RAM
- Add Redis for queue (replacing in-Postgres queue) when extraction
  throughput exceeds 100 invoices/min
- Add separate worker dynos for extraction (decouple from web)
- Add read replica when read-heavy reporting endpoints introduced

### Deployment

- Single Docker Compose stack on the EC2 box: `app`, `worker`,
  `postgres`, `redis`, `caddy` (reverse proxy + TLS via Let's Encrypt).
  MinIO is dev-only — production uses S3 directly via the same boto3
  config (`S3_ENDPOINT_URL=https://s3.eu-central-1.amazonaws.com`).
- CI pipeline (GitHub Actions): on push to `main` → run tests → build
  Docker image → push to registry → SSH to EC2 → `docker compose pull && docker compose up -d`
- Database migrations: Alembic auto-run on container start
- Zero-downtime: not in V1 (acceptable: ~10s blip on deploy). Add
  blue-green post-V1.

---

## 12. Security & compliance (RODO/GDPR)

### Data classification

| Class | Examples | Handling |
|---|---|---|
| Public | Marketing pages | No restrictions |
| Internal | Aggregate usage metrics | Internal access only |
| Confidential | User account data, billing info | Encrypted at rest, TLS in transit |
| Sensitive | Invoice PDFs, extracted invoice data | Encrypted at rest, EU-only, audit logged, customer-deletable |

### RODO/GDPR specifics

- **Legal basis for processing:** contract performance (Art. 6(1)(b))
  for paid customers; explicit consent (Art. 6(1)(a)) for free tier on
  signup
- **Data Processing Agreement (DPA):** templated, auto-generated on
  signup, signed click-through, downloadable PDF kept on file
- **Right to access (Art. 15):** "Export my data" button in settings
  dumps all account data as ZIP (JSON + original PDFs)
- **Right to erasure (Art. 17):** "Delete account" → soft-delete
  immediately, hard-delete after 30-day grace period (during which
  restore is possible); hard-delete purges S3 objects + DB rows + backups
  within 90 days
- **Data residency:** all PDFs, extracted data, backups stored in EU
  (AWS eu-central-1 — EC2 + S3 + Bedrock all in Frankfurt; Postmark EU).
  No US data flow.
- **Sub-processors disclosure:** public list at `/legal/subprocessors`:
  AWS (EC2 hosting + S3 storage + Bedrock LLM), Stripe (payments),
  Postmark (email). Customer notified 30 days before any sub-processor
  change.
- **Breach notification:** procedure documented, 72-hour notification
  to UODO per Art. 33
- **Audit log:** every extraction, every export, every user-facing
  action with timestamp + user_id + IP + action. Retention 24 months.
  Immutable (append-only DB table).

### Security controls

- TLS 1.3 everywhere (Caddy auto-renews Let's Encrypt)
- Argon2id for passwords
- API keys: stored as bcrypt hash, shown plaintext once on creation
- S3 objects: server-side encryption with AWS-managed keys (SSE-S3)
  (customer-managed keys for Business+ tier later)
- Secrets management: `.env` file on the VPS, root-readable only;
  secrets rotated quarterly
- No SSH password auth; SSH keys only; fail2ban on the VPS
- Dependency scanning: Dependabot via GitHub
- Penetration test: not in V1 (cost); before going from "paper" to
  "real money customers" later

### Insurance consideration

Cyber liability insurance ~€500-2000/yr. Not needed for V1; revisit
once enterprise customers ask for proof.

---

## 13. Operations

### Logging

Same pattern as the trading bots: structured JSON, one event per line,
sent to stdout and rotating files. Events include:

- `extraction.started`, `extraction.completed`, `extraction.failed`
- `validation.warning`, `validation.error`
- `export.requested`, `export.delivered`
- `auth.signup`, `auth.login`, `auth.failed`
- `billing.subscription_created`, `billing.payment_failed`

Aggregator: Better Stack (Logtail) or self-hosted Loki + Grafana — same
trade-off as trading bots.

### Monitoring

- UptimeRobot: ping `/health` every 60s from 3 EU regions. Alert via
  Discord webhook on any failure.
- Sentry: backend error tracking. Free tier covers V1 volume.
- Custom Discord alerter (port the `scripts/run_live.py` pattern from
  the trading bots): hourly digest of:
  - Invoices processed (success / failure counts)
  - New signups / churn
  - Payment events
  - Any extraction with confidence < 0.50 (potential model regression
    signal)

### Alerting tiers

| Severity | Trigger | Channel |
|---|---|---|
| Critical | Service down, payment processor down, DB unreachable | Discord @here + email |
| Warning | Extraction error rate >5% over 1h, queue depth >100 | Discord channel |
| Info | New paid signup, plan upgrade | Discord channel (separate) |

### Backups

- Postgres: `pg_dump` nightly at 03:00 UTC → encrypted → uploaded to a
  separate S3 bucket in a different region (eu-central-1 primary →
  eu-west-1 Ireland backup)
- Retention: 30 daily, 12 monthly, 7 yearly
- Restore drill: monthly, automated, brings up a parallel container and
  asserts row counts match within 1%
- S3 objects (PDFs): versioning enabled + cross-region replication

### On-call

Solo dev = always on-call. SLAs by tier:

- Free tier: best-effort, no SLA, no guaranteed response
- Starter / Pro: 24h response time on weekdays
- Business: 4h response time weekdays
- Custom Biuro: contracted SLA

**Operational warning:** plan vacation tests. After V1.2 launch and
paying customers exist, take 2 weeks off and see if the bot keeps
running. If it can't, the architecture isn't ready for real customers.

---

## 14. Pricing model — v1.3 (beta-first)

(See §2 for the table; this section is the canonical version.)

**v1.3 pivot:** the project seeds with a $10 Claude budget. Revenue
from paying beta users refills the cap as it depletes — the floor is
bootstrap, not steady state. A traditional free tier (25/mo × N
accounts) is not affordable from the seed alone, so beta is paid-only;
the 3-lifetime-uploads free tier returns in V1.4 once paying customers
are underwriting Bedrock.

**Beta tiers (V1.3, no monthly plans yet):**

- **Beta pay-as-you-go** — $5 minimum top-up — **0.50 PLN per invoice**
  (~$0.13). Phone-verified accounts only. Card on file via Stripe
  SetupIntent. No free uploads in beta — every invoice is billable.

**Post-beta tiers (V1.4+, once budget moves to customer-funded):**

- **Free** — 0 PLN — 3 invoices total (lifetime, not per-month) —
  JPK_FA, JSON — phone-verified only — 1 concurrent upload
- **Starter** — 79 PLN — 500 invoices/mo — + CSV/XLSX — 5 concurrent
- **Pro** — 199 PLN — 2 000 invoices/mo — + EDI++, hot-folder — Yes API, 20 concurrent
- **Business** — 599 PLN — 10 000 invoices/mo — + priority queue — Yes API, 100 concurrent
- **Biuro** — custom — 10k+ — All + multi-client workspace — Yes API

Overage: 0.10 PLN per invoice above plan, capped at 2× plan limit.

Plan changes: prorated, immediate effect (Stripe handles this).

Refunds: 30-day money-back on first paid month, no questions. Removes
purchase friction.

### Unit economics — Haiku-only (V1.3)

With Haiku-only routing (SPEC §6) and the editable review page as the
correction layer, per-invoice cost stabilises near the Haiku floor:

| Scenario | Avg LLM cost | Beta charge | Margin |
|---|---:|---:|---:|
| Haiku-only, no re-extract | $0.004 = 0.016 PLN | 0.50 PLN | ~97% |
| Haiku + operator re-extracts on Sonnet (10%) | $0.0052 = 0.021 PLN | 0.50 PLN | ~96% |
| Haiku + operator re-extracts on Sonnet (50%) | $0.010 = 0.040 PLN | 0.50 PLN | ~92% |

At 0.50 PLN charge per invoice, even pathological 50% Sonnet re-extract
rate keeps margin above 90%. The $10 project Bedrock budget covers
~2500 invoices of pure-Haiku usage before refilling.

**Budget guardrail:** track cumulative Bedrock token spend per week
(payload of the `invoice.extracted` audit event already records token
counts; sum + multiply by published unit prices). Alert via Discord
when projected month-end spend exceeds the AWS Bedrock cap set in the
console. A sustained re-extract rate above 30% is a hard incident:
investigate whether Haiku-only extraction is regressing on a new
invoice template family, do not silently absorb the cost.

---

## 15. Build phases — definitions of done

This is the implementation checklist. Each phase has a crisp "done
when" so a fresh Claude session can pick up the next item without
ambiguity. Phases are sequential; do not start phase N+1 until phase N
is done.

### Phase 0 — Customer-research validation (PRECONDITION)

Done when:

- [ ] Operator has interviewed at least 5 Polish accountants (biura
      rachunkowe or solopreneur księgowi)
- [ ] At least 3 of those have said explicitly: "I would pay for this"
      at the proposed Starter tier (79 PLN) or higher
- [ ] At least 3 of those have named at least one of these as the
      hook: Saldeo too expensive, Saldeo clunky UX, foreign-invoice
      handling, paragony handling, JPK_FA generation, direct Subiekt
      export
- [ ] Operator has gathered ≥50 real Polish invoice PDFs (mix of
      faktury VAT, paragony, foreign invoices, korekty) for the
      extraction prototype to validate accuracy against. The set
      must cover at least 3 distinct seller layouts to test
      cross-template robustness.

Do not start phase 1 until all four checkboxes are confirmed by the
operator. If validation fails, stop the project and pick a different
venture.

### Phase 1 — Extraction prototype

Done when:

- [ ] Single-file Python script at `scripts/extract_prototype.py`
- [ ] Takes a PDF path as CLI arg
- [ ] Calls Claude vision (Bedrock or direct Anthropic API as
      placeholder) with a structured-output prompt matching
      `CanonicalInvoice`
- [ ] Prints valid `CanonicalInvoice` JSON to stdout
- [ ] Includes confidence scores per field
- [ ] Validates NIP/REGON checksums and VAT math, populates warnings
- [ ] Tested manually against all ≥50 of the real invoices gathered in phase 0
- [ ] Operator reviews accuracy via `scripts/spotcheck.py`: extraction
      must hit ≥85% "good" verdicts on header fields and ≥90% on totals,
      across the **in-scope** test set (documents moved to
      `eval_set/_noise/` by curate, and out-of-scope categories per §16,
      are excluded from the denominator)
- [ ] Haiku-first routing measured: log the per-invoice path
      (Haiku-only vs Haiku-then-Sonnet) on the eval run and report
      the hit rate. <60% triggers the margin-sensitivity review in §14
      before any further phase is started.
- [ ] If accuracy fails, iterate on prompt; if still fails after 3
      iterations, escalate to operator (may indicate fundamental
      problem)

### Phase 2 — Repository scaffold + V1.0 backend skeleton

Done when:

- [ ] `pyproject.toml`, `mypy.ini`, `docker-compose.yml`, `alembic/`
      set up
- [ ] FastAPI app at `src/app/main.py` with `/health` endpoint
- [ ] PostgreSQL container in docker-compose; Alembic baseline migration
- [ ] User + Org SQLAlchemy models, alembic migration
- [ ] Email/password auth flow: signup, email verification (Postmark
      stub), login, password reset
- [ ] Session cookie + CSRF protection
- [ ] `pytest tests/` passes
- [ ] `mypy --strict src/` clean
- [ ] CI workflow in `.github/workflows/ci.yml` runs both on every PR

### Phase 3 — V1.0 extraction pipeline

Done when:

- [ ] Web upload endpoint at `POST /app/upload` accepts PDF, stores to
      AWS S3 (or MinIO in dev), creates DB row
- [ ] Background worker (`src/app/workers/extract.py`) picks up new
      uploads, runs the extraction prototype's logic (now extracted to
      `src/pipeline/extraction/`), persists `CanonicalInvoice`
- [ ] Validation layer in `src/pipeline/validation/`: NIP, REGON,
      VAT math, totals reconciliation
- [ ] Pipeline tests using fixtures of known invoice PDFs
- [ ] `mypy --strict` clean

### Phase 4 — V1.0 web UI

Done when:

- [ ] Jinja templates + HTMX for: dashboard, upload, invoice list,
      invoice review page, settings, billing
- [ ] PDF preview via PDF.js on review page
- [ ] Field-by-field edit with confidence pills
- [ ] Save corrections → patches CanonicalInvoice
- [ ] Tailwind CSS styling, Polish-language UI strings
- [ ] Playwright E2E tests for the upload-extract-review-export flow

### Phase 5 — V1.0 JPK_FA export

Done when:

- [ ] `src/pipeline/export/jpk_fa.py` generates valid JPK_FA(4) XML
- [ ] XSD validation step before delivery; errors surface to user
- [ ] `src/pipeline/export/json_export.py` dumps CanonicalInvoice as JSON
- [ ] Export endpoint `POST /api/v1/invoices/{id}/exports` and
      `GET /api/v1/exports/{id}`
- [ ] Web UI "Export as JPK_FA" / "Export as JSON" buttons trigger
      download
- [ ] Unit tests covering JPK_FA generation against ≥3 reference invoices
- [ ] Round-trip test: parse the generated XML back, assert it matches
      input

### Phase 6 — V1.0 billing & launch

Done when:

- [ ] Stripe integration: customer creation on signup, Checkout for
      plan change, Customer Portal link, webhook handlers
- [ ] Usage metering: per-org counter, plan-limit enforcement at
      upload time, overage tracking
- [ ] Free tier vs Starter tier gating works correctly
- [ ] DPA template signed click-through at signup
- [ ] Landing page at `/` with pricing, signup CTA
- [ ] Deployed to AWS EC2 t2.micro via Docker Compose
- [ ] TLS via Caddy + Let's Encrypt
- [ ] Discord alerter (`scripts/ops_alerter.py`) scheduled to run
      hourly on the VPS
- [ ] V1.0 launched: operator personally invites the interviewed
      accountants

### Phase 7 — V1.1 CSV/XLSX

Done when:

- [ ] `src/pipeline/export/csv_xlsx.py` generates both layouts (wide
      + long) for CSV
- [ ] XLSX generator produces 3-sheet workbook
- [ ] Column-map template engine: user picks fields, names, order
- [ ] Pre-shipped templates: `subiekt_gt`, `comarch_optima`, `ifirma`,
      `generic`
- [ ] Bulk download endpoint (zip of N invoices)
- [ ] Web UI updates: format selector, template manager
- [ ] Unit tests covering each layout and template
- [ ] Released to existing users; Starter tier gets CSV/XLSX unlocked

### Phase 8 — V1.2 EDI++ + ingestion expansion

Done when:

- [ ] `src/pipeline/export/edi_pp.py` generates valid Insert EDI++
      with Windows-1250 encoding
- [ ] Round-trip parse validation
- [ ] Manual import test into Subiekt GT demo: invoice appears with
      correct fields
- [ ] SFTP hot-folder ingestion: per-user credentials, watcher process
      picks up new files
- [ ] Email-forwarding ingestion: per-user inbox alias, attachment
      handling, reply-with-results
- [ ] Pro tier unlocks EDI++ + ingestion features
- [ ] Usage dashboard: invoices processed, success rate, average
      confidence, monthly cost
- [ ] V1.2 launched publicly (Product Hunt PL, r/Polska, accountant
      communities)

---

## 16. Out of scope (explicit non-goals for V1)

These will come up in customer conversations. Refuse politely and note
for post-V1.

- **Direct KSeF submission** — too much per-customer auth ceremony for
  V1; we generate the XML, they upload via their existing tool
- **Custom field extraction** — only the canonical schema; no "I need
  field X that's not in your model"
- **Handwritten receipts / scanned-on-phone receipts** — V1 assumes
  printed PDF input
- **OCR for non-Latin scripts** — Polish-language Latin scripts only
- **On-prem deployment** — cloud SaaS only; no self-hosted option
- **Multi-user / role-based access** — single user per org in V1
- **Workflow automation** (approvals, routing) — different product
  category
- **Invoice generation** (sending invoices) — we extract, we don't
  generate
- **Integration with banking for reconciliation** — post-V1
- **AI chatbot interface** — no
- **Native mobile apps** — PWA at most, post-V1; native much later if ever
- **Comarch Optima / Symfonia direct integration** — post-V1; CSV/XLSX
  covers them at V1.1 level
- **E-commerce receipts with VAT-inclusive product pricing and a
  separate shipping VAT** — observed in Phase 1 eval (GanjaFarmer
  W074234): products listed at `Cena netto = Cena brutto` with the
  document's `Razem VAT` line reflecting only the shipping VAT. The
  canonical schema cannot cleanly express "products at vat_rate=zw
  plus shipping at vat_rate=23" because shipping isn't a first-class
  line item. V1 treats these as `PARAGON` with a hard validation
  warning; operator corrects in the Phase 4 review UI. Revisit in V2
  with an explicit `shipping_lines` field if volume warrants.
- **Separate OFFER / quote (oferta) enum** — V1 maps documents
  labelled "oferta" / "quote" / "wycena" to `InvoiceType.PRO_FORMA`.
  Both are non-binding price proposals and the downstream JPK_FA
  mapping is identical. Dedicated `OFFER` enum value deferred to V2.

---

## 17. Risks & open questions

### Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Saldeo drops price to undercut | Medium | High | Differentiate on UX + API + price-for-volume; not race to bottom |
| KSeF mandate compresses TAM faster than expected | Medium | High | Pivot toward foreign-invoice / paragon use cases (which KSeF doesn't cover) |
| Claude pricing rises 2-3× | Low | Medium | Multi-provider abstraction layer; fall back to GPT-4o-mini, Gemini, or Bielik-vision when available |
| Polish-specific extraction accuracy regression on a model upgrade | Medium | Medium | Held-out eval set of 500 labeled invoices; A/B before promoting new model |
| RODO complaint from a user | Medium | High | Strong DPA, clear audit trail, immediate-response procedure |
| Solo-dev burnout | High | High | Hard cap at V1.2 scope; if not profitable after V1.2 has been live for 6 months, kill or sell project |
| Insert / Comarch / Symfonia changes their format | Low | Medium | Versioned export adapters; format detection from sample customer files |
| Stripe account closure | Low | High | Maintain backup Przelewy24 direct integration ready to switch |
| Server compromise leaks invoice data | Low | Critical | Encryption at rest, principle of least access, regular dependency updates, cyber insurance once enterprise customers |

### Open questions — require operator decision before phase 6

1. ~~**Polish business entity**~~ — **DECIDED 2026-05-13:** JDG
   (jednoosobowa działalność gospodarcza). Operator trades as a sole
   proprietor; billing entity = operator's JDG; RODO controller =
   operator personally. sp. z o.o. revisit point: when annual revenue
   approaches the JDG ZUS contribution ceiling or when first
   enterprise customer demands corporate counterparty.
2. ~~**VAT registration**~~ — **DECIDED 2026-05-13:** registering
   VAT-czynny from day 1. Customer-facing prices in `SPEC.md` and
   `index.html` are gross — split is handled by Stripe (PLN currency,
   23% VAT line on the invoice).
3. ~~**Product name + trademark**~~ — **DECIDED 2026-05-13 (working
   name):** `Faktomat` (faktura + automat). Tagline "Wsadź fakturę,
   wyjmij JPK." Pending: 30-min trademark check on
   `https://ewyszukiwarka.pue.uprp.gov.pl/` before printing on physical
   materials / before applying for a wzór przemysłowy. Web/beta launch
   does not block on this.
4. **PDF/A archival** — some accountants need their PDFs in PDF/A
   format for compliance. Do we convert on ingestion? V1 punts; revisit
   post-V1 based on user demand.
5. **NIP-EU validation** — for EU-cross-border B2B reverse charge, VAT
   IDs must be validated via VIES. Cache responses (24h) to avoid rate
   limits. Decide phase: V1.0 or V1.1.
6. **Multi-currency on the same invoice** — does it happen in practice?
   Rare. Treat as out-of-scope V1; refuse extraction with a clear error
   if detected.
7. **Where does the development DB live during phases 2-5?** Local
   Postgres in Docker Compose is fine for dev. Decide if there's a
   shared staging DB or if everything is local-only until phase 6
   deploy.

### Decisions recorded (previously open)

8. **Anthropic access path.** **DECIDED:** direct Anthropic API for
   the Phase 1 prototype (faster to start; Phase 1 uses only operator-
   supplied test invoices, not customer data, so US data flow is
   acceptable). **Switch to AWS Bedrock `eu-central-1` no later than
   Phase 3**, when the worker first processes uploaded user PDFs —
   EU data residency is non-negotiable from the moment a real user's
   invoice enters the pipeline. The extraction module must therefore
   be written behind a thin provider interface from day one so the
   Phase 3 switch is a config change, not a rewrite.

### Open questions — require operator decision before phase 8

9. **Will the operator personally test against Subiekt GT demo for
   phase 8?** If yes, the operator needs to download a Subiekt GT
   demo licence ahead of time. If no, we ship phase 8 without
   end-to-end validation, which is risky.

---

## End

This spec is the contract between the operator and any Claude session
working on the project. Update this file when scope changes; do not
let the conversation drift away from what's written here. If a
discussion in chat produces a decision, write it into this document
before treating it as settled.
