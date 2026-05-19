# Changelog — pre-Phase-6 archive

Build chronology for invoice_idp/Faktomat from 2026-05-12 (Phase 0)
through 2026-05-14 mid-day (Phase 6 wiring). The closing "Phase 6 LIVE"
entry and everything after it live in [`CHANGELOG.md`](CHANGELOG.md).

Read top-down for newest-first within this archive (matches the active
CHANGELOG convention). Phase progression in this file: Phase 0 (eval)
→ Phase 1 (extraction prototype) → Phase 2 (auth) → Phase 3 (Bedrock +
worker) → Phase 4 (web UI) → Phase 5 (JPK_FA export) → Phase 6 wiring
(billing scaffold, settings page, Stripe webhook, upload gate, phone
verify endpoints, top-up flow, doc-sync, add-row UI, VM migration
prep).

---

## 2026-05-14 — `enqueue.py` utility for one-off re-extraction

When an invoice gets stuck in `status=pending` (worker was down at
upload time) or `status=failed` (transient error, prompt missing,
model access etc.), the operator needs to retry the extraction
without waiting for a fresh upload or wiring up the
"Re-ekstrakcja (Sonnet)" UI on the failed-status detail page.

`invoice_idp/enqueue.py` is a small CLI that talks straight to the
arq pool and adds an `extract_invoice_task(invoice_id)` job to the
Redis queue. Defaults to `redis` as the hostname so it runs inside
the worker container with zero env-var setup. Override with
`REDIS_HOST=localhost` if you ever want to run it from the host
directly (outside compose).

Dockerfile now copies `enqueue.py` into `/app/` alongside `src/`,
so the call shape is just:

```powershell
docker compose exec worker python enqueue.py <invoice_id>
# or with explicit model:
docker compose exec worker python enqueue.py <invoice_id> claude-sonnet-4-6
```

The model argument is optional — without it the worker uses the
default Haiku-first routing. With it, the same hook the
"Re-ekstrakcja (Sonnet)" UI button uses fires (force_model bypasses
Haiku routing).

After merging: `docker compose up -d --build worker` to bake the
new image.

## 2026-05-14 — Dockerfile: copy `prompts/` into the image

Worker-in-compose (previous entry) revealed a pre-existing bug in
the Dockerfile: `prompts/` wasn't being copied. The host-run worker
+ host-run uvicorn never noticed because they read the file straight
off the working tree. Inside the container the worker crashed on the
first job with:

```
FileNotFoundError: [Errno 2] No such file or directory: '/app/prompts/extraction_v1.md'
```

`anthropic_provider.py:21` resolves `PROMPT_PATH` as
`Path(__file__).resolve().parents[3] / "prompts" / "extraction_v1.md"`
— inside the image that's `/app/prompts/extraction_v1.md`. The same
path applies to `BedrockExtractor` because the Bedrock provider
shares `_load_prompt()` with the direct-Anthropic one.

Fix: `COPY prompts/ ./prompts/` in `Dockerfile` between the existing
`schemas/` copy and the healthcheck.

After merging: `docker compose up -d --build worker` (image rebuild
picks up the new COPY).

## 2026-05-14 — arq worker in dev compose (no more "where's my second terminal")

Symptom: operator's first live upload after the upload-gate fix went
through `POST /app/wgraj` → 303 redirect → invoice-detail stub
auto-refreshing every 3s with status stuck in `pending`. Root cause:
the worker has been a `python -m arq …` invocation in a second
PowerShell window the whole time, and that window wasn't open after
the redeploy. Every new paying user would have hit the same wall.

### Change

`docker-compose.yml` now defines a `worker` service that builds from
the existing prod `Dockerfile` and overrides CMD to
`python -m arq src.app.workers.extract.WorkerSettings`. The image is
the same one prod compose uses for app + worker, so the build cache
stays warm.

Networking: the host-side uvicorn keeps using its `.env` DATABASE_URL
/ REDIS_URL pointing at `localhost`. The worker container needs to
reach sibling containers, so its `environment:` overrides those three
URLs to use Compose service hostnames (`postgres:5432`, `redis:6379`,
`http://minio:9000`). Both the host uvicorn and the containerised
worker end up hitting the same Postgres + Redis + MinIO instances.

The remaining secrets (`ANTHROPIC_API_KEY`, `AWS_*`, `POSTMARK_*`,
`BIGGA`) come from `env_file: .env` — operator's existing dev `.env`
covers them.

`restart: unless-stopped` means the worker survives crashes and
Docker Desktop startup — operator never has to run `python -m arq`
manually again.

### Migration

```powershell
cd "E:\CLAUDE CODE PLAYSPACE\invoice_idp"
git pull origin main

# stop the hand-run worker in the second terminal (Ctrl-C)
# then:
docker compose up -d --build worker
docker compose logs -f worker      # confirm "Starting worker for …"
```

Rebuild only needed when `src/app/workers/*`, the extractor, or the
models change — uvicorn-on-host stays a separate iteration loop for
everything else.

## 2026-05-14 — Upload-gate phone-verify relaxation + test/template drift fixes

Closes the single remaining code task between "Phase 6 wired" and
"Faktomat takes new paying users". Phone-verify deferred to Phase 7+
(see prior entry) means `phone_verified_at IS NOT NULL` in the
upload gate would 403 every new signup. Gate now requires only what
we actually verify: a confirmed email + a non-zero balance.

### Upload-gate (`src/app/web/routes.py`)

- `_upload_gate()` now checks `user.email_verified` instead of
  `phone_verified_at`. Redirect target changed from
  `/app?reason=phone` to `/app?reason=email_verify`. Docstring
  updated to spell out the Phase-7+ revisit trigger.
- `POST /app/wgraj` no longer needs its own `email_verified` check —
  `_upload_gate()` is now single source of truth for both GET and
  POST, eliminating a small asymmetry.

### Layout (`src/app/templates/app/_layout.html`)

- Removed the violet phone-verify banner. Email-verify banner (amber)
  stays as the sole pre-upload friction surface.
- Comment near the top updated to point future sessions at the
  HANDOFF "Twilio: deferred" section, with a note on how to restore
  the banner if/when SMS-OTP comes back.

### Tests (`tests/integration/test_upload_gate.py`)

- Rewritten end-to-end against the new gate. `_set_phone_verified`
  helper removed; new `email_verified` flag in `_signup_and_login`
  controls whether the gate passes. Six tests cover the three
  states (no-email-verify, no-credit, gate-passes) × {GET, POST} +
  the layout banner.

### Pre-existing failures discovered along the way

Running the full suite to verify my changes surfaced 7 unrelated
failures from the 2026-05-13 → 2026-05-14 design-refresh PRs (#3/#4
sequence touched templates but not tests). Fixed in the same commit
since they were blocking a clean run:

- **`tests/integration/test_settings.py` (6 failures)** — `POST
  /app/ustawienia` rejected requests with empty `nip` / `regon` /
  `kod_urzedu` as 422 missing-field. Root cause: FastAPI/Pydantic
  v2.13 treats empty string form fields as "field missing" unless a
  default is set. Endpoint signature now has `= ""` defaults for the
  three optional fields, matching the `_validate_settings_form`
  semantics that already documented these as optional at save time.
- **`tests/integration/test_billing.py::test_billing_get_renders_…`
  (1 failure)** — assertion `"12.34 zł" in body` no longer matched
  the redesigned template (renders `12.34<span>PLN</span>`). Updated
  assertions to check pieces separately + assert on hidden
  `amount_grosze` inputs instead of fragile inline numbers.

### Verification

- `mypy --strict src/` — 0 errors in 46 source files.
- `pytest tests/` — **130/130 passing** in 74s against the live
  Docker stack (Postgres + Redis + MinIO healthy; migration `0004`
  applied).
- `python3 scripts/audit_secrets.py` — exit 0.

## 2026-05-14 — Doc-sync (round 2): sandbox `.env` ≠ prod `.env`

Operator correction after the doc-sync entry below: first live 20 PLN
top-up already cleared 2026-05-13 evening end-to-end (Checkout →
webhook 200 → `credit_balance_grosze` increment in Docker'd Postgres).
This contradicts the sandbox `.env` view I was working from, which
showed `STRIPE_SECRET=` (a key no code path reads — `config.py`
expects `STRIPE_SECRET_KEY`) and no `STRIPE_WEBHOOK_SECRET`. The
explanation is **divergence between sandbox and prod `.env` files**:
`.env` is gitignored, the sandbox copy is stale (2026-05-13 22:33),
and the operator's prod `.env` on the Windows host has the correct
`STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` values that the
2026-05-14 code requires.

**Lesson for next session: do not infer prod env state from sandbox
`.env`.** Source of truth is `.env.example` (committed) for shape +
runtime `docker compose exec app env | sort` on the prod host for
actual values. HANDOFF and CLAUDE.md routing both updated to spell
this out.

This entry also corrects the "Suggested next action" in HANDOFF:
`STRIPE_WEBHOOK_SECRET` is no longer a launch blocker (it's already
set in prod). The only remaining code task before "Faktomat is taking
new paying users" is upload-gate phone-verify relaxation.

## 2026-05-14 — Doc-sync + Twilio deferred to Phase 7+

Operator opened the session with: "stan faktomatu jest inny niż
zareportowany w dokumentacji". Audit confirmed: HANDOFF.md TL;DR was
multiple PR cycles stale — claimed "44 source files / 53 tests / one
commit pushed / uncommitted local work" while the actual state was
46 src files, 130 test functions, 4 merged PRs + several direct
commits on `main` (PL copy rewrite, FAQ fix, app shell redesign,
pre/post-login design unification, 3b' add-row UI). CLAUDE.md routing
still listed Twilio as a launch blocker.

### Operator decision

**Twilio Verify is deferred to Phase 7+ ("maybe").** Rationale (operator):
phone-verify SMS-OTP is anti-fraud surface that only earns its keep
on a free tier. Beta is paid-only PAYG (0.50 PLN/invoice) gated by
Stripe Checkout top-up + email-verify, which already covers the realistic
fraud surface for warm Polish-accountant leads. Reintroduce only if we
introduce a free-scan tier.

### Doc changes

- **`invoice_idp/HANDOFF.md`** — rewrote TL;DR, "What's pending",
  "Phase 6 launch gate", "Suggested next action", and the trailing
  verification blurbs to reflect post-PR-#4 state. Added "Twilio:
  deferred to Phase 7+" decision section with revisit trigger.
  Replaced the "one commit pushed / uncommitted local work" claim
  with the actual git-log state. Updated test count (130) and src
  file count (46). Replaced the Windows-specific `~/.claude/projects/
  E--CLAUDE-CODE-PLAYSPACE/` memory path with the current
  `/home/ewwe/playspace/` equivalent.
- **`random/CLAUDE.md`** — updated the invoice_idp routing line to
  reflect "Phase 6 wiring DONE, Twilio deferred". Rewrote the
  "Still missing for invoice_idp Phase 6 launch" section so only
  `STRIPE_WEBHOOK_SECRET` is on the operator one-shot list.

### Code state (no changes this entry)

Nothing in `src/` touched. The phone-verify endpoints, the
`/app/wgraj` phone gate, the `sms.py` Protocol, and the
`0004_billing` phone columns all remain in tree as dormant code.
The follow-up code task (relax `/app/wgraj` to
`email_verified_at AND credit_balance_grosze > 0`) is captured in
HANDOFF "Suggested next action §1" — not landed yet.

## 2026-05-14 — Phase 4 chunk 3b': add-row UI on review page

Closes the last named Phase-4 UI gap. Until now the review page was
delete-only — if OCR missed a line item or VAT bucket, the operator had
to either re-extract (Sonnet, $) or ship JPK_FA with bad data. Now they
can append rows directly. Implemented while a parallel Claude session
worked on fiszkomat content; isolated to faktomat files only.

### Frontend (`src/app/templates/app/invoice_review.html`)

- `+ Dodaj pozycję` button under the line-items table; clones from a
  hidden `<template id="line-row-template">` with sensible defaults
  (quantity=1, unit=`szt.`, vat_rate=`23`, all amounts=`0.00`).
- `+ Dodaj stawkę` button under the VAT-summary table, same pattern with
  `<template id="vat-row-template">`.
- Templates wrap the `<tr>` in `<table><tbody>` so the browser parser
  doesn't strip it; runtime clones the `<tr>` only.
- `refreshLineCount()` re-numbers visible rows on add and remove, so
  the index column stays correct after edits. Existing rendered rows
  gained `data-line-index` to participate in renumbering.
- Click handler unified: `data-action="add-line|add-vat|remove-line|
  remove-vat"` routes through one delegated listener; new rows focus
  the first input/select for keyboard-only operators.

### Server contract — no changes

The serialiser already filtered `[data-row]:not([data-removed])` and
read `dataset.unitPriceNet`/`discountPct` with `'0'` fallback, so
cloned rows post a longer `lines`/`vat_summary` array without any
backend code change. CanonicalInvoice schema validates the longer
array; validate_invoice runs the same hard/soft checks.

### Tests (`tests/integration/test_corrections.py`)

- `test_corrections_accepts_added_line` — append one line, bump totals
  + vat_summary to match, assert persistence and 0 hard warnings.
- `test_corrections_accepts_added_vat_summary_entry` — append one VAT
  bucket plus a matching line, assert both rates round-trip.

### Verification

Audit-secrets clean. `py_compile` clean on the test module. **mypy +
pytest not run in this sandbox** (project deps not installed); run on
the operator's dev box per HANDOFF Runbook before push.

## 2026-05-13 — Phase 4 chunk 4: Settings page + redis.aclose cleanup

Operator goal: "make profit". Highest-leverage in-sandbox unit was the
named Phase-4 blocker — without `Org.kod_urzedu`, JPK_FA export returns
422, which gates the feature that turns "OCR demo" into "thing a
Polish accountant will actually pay for".

### Phase 4 chunk 4 — `/app/ustawienia`

- `src/utils/kod_urzedu.py` — 4-digit shape validator (Ministry list
  not bundled; the file changes on its own clock, but the canonical
  NNNN shape catches the common operator mistakes upfront).
- `src/app/templates/app/settings.html` — Polish form for org name,
  NIP, REGON, KodUrzedu with per-field explainers; success and
  validation-error banners.
- `src/app/templates/app/_layout.html` — added `Ustawienia` to the
  top nav between `Wgraj` and the user-menu divider.
- `src/app/web/routes.py` — `GET /app/ustawienia` renders with current
  values; `POST /app/ustawienia` runs `_validate_settings_form` (NIP
  checksum, REGON 9/14-digit checksum, KodUrzedu 4-digit shape — empty
  values allowed at save, only required at JPK_FA export), persists
  diff to `Org`, emits one `org.settings_updated` audit event with
  the `changed_fields` list when there's an actual diff.
- `tests/unit/test_kod_urzedu.py` — 4 cases (valid, normalize, length,
  non-digit).
- `tests/integration/test_settings.py` — 8 cases: login gate,
  pre-fill, persist+audit, bad-kod 400, bad-NIP 400, missing-name 400,
  CSRF gate, no-op no-audit.

### Outstanding-cleanup #3

- `src/app/api/invoices.py:78` — `await redis.close()` →
  `await redis.aclose()` per Redis SDK ≥5.0.1 deprecation.

### Phase 6 Stripe webhook + worker debit

`POST /webhooks/stripe` (new in `src/app/api/webhooks.py`, registered
in `src/app/main.py`) accepts a Stripe event, verifies signature when
`STRIPE_WEBHOOK_SECRET` is set (lazy-imports the `stripe` SDK; dev
mode without secret trusts the body with a warning log), routes
`checkout.session.completed` with `payment_status=paid` to the
`_credit_topup` helper. Idempotency uses the audit table as source-
of-truth: a matching `billing.topup_credited` event with the same
`topup_idem_key` in `payload->>'idem_key'` short-circuits the
credit. Other event types and unpaid sessions are 200-OK acknowledged
so Stripe doesn't retry them.

`stripe_client.create_topup_session` now sets the `topup_idem_key` on
session-level metadata as well as payment-intent-level — the webhook
can read it straight off `data.object.metadata` without an extra PI
fetch.

`src/app/workers/extract.py` debits `Settings.invoice_price_grosze`
from `Org.credit_balance_grosze` on successful extraction and emits
a `billing.extraction_debited` audit event with the post-debit
balance. Per HANDOFF: the in-flight job completes even if balance
goes negative — the upload gate is the enforcement point, the worker
just records.

7 new integration tests in `tests/integration/test_webhook_stripe.py`
covering: completed-paid credits balance + audit, replay is no-op
(2 deliveries → 1 credit), non-completed events 200-ignored, unpaid
sessions ignored, missing idem_key 400, malformed body 400,
orphan customer 200-noop.

### Phase 6 upload gate

`/app/wgraj` (both GET and POST) now requires
`user.phone_verified_at IS NOT NULL` AND `org.credit_balance_grosze > 0`.
Failed gates 303 to:
- `/app?reason=phone` — phone not yet verified
- `/app/billing?reason=empty` — balance ≤ 0

A new global banner in `app/_layout.html` shows up on every
authenticated page while phone is unverified (mirrors the existing
email-not-verified pattern). Billing page renders a contextual
`?reason=empty` notice with a doładuj CTA.

Helper `_upload_gate(request, session, user)` lives in `web/routes.py`
above `upload_page`; returns `Response | None` so the call sites can
short-circuit clean.

6 new integration tests in `tests/integration/test_upload_gate.py`:
GET redirects (no phone, empty balance), GET success when gate
passes, POST redirects (no phone, empty balance) — verified the
gate fires *before* file parsing — and the global phone banner
shows on /app while unverified.

### Phase 6 billing top-up flow

`GET /app/billing` shows balance + three top-up buttons (20/50/100 PLN
per HANDOFF). `POST /app/billing/topup` validates amount against a
server-side allowlist (no arbitrary-amount tampering), lazily stamps
`Org.stripe_customer_id` via `ensure_customer`, generates a fresh
UUID idempotency key per request, creates the Checkout Session via
`get_stripe_client().create_topup_session(...)`, 303s to the returned
URL. ConsoleStripeClient returns `http://localhost:8000/__dev/checkout/cs_dev_*`
in dev; swapping to RealStripeClient is automatic when
`STRIPE_SECRET_KEY=sk_test_*` is in `.env` (already set per ROUTING
inventory).

`app/billing.html` shows pretty-printed PLN balance, three amount
buttons annotated with "≈ N ekstrakcji" (uses
`Settings.invoice_price_grosze=50` to compute), and success/cancel
result banners reading `?result=ok|cancel` from Stripe-side
redirects. Nav link added between Ustawienia and the user-menu
divider.

7 new integration tests in `tests/integration/test_billing.py`:
login-required GET, balance+amounts render, 303→checkout URL +
audit + customer-id stamp, arbitrary-amount rejection, CSRF gate,
customer-id reuse across multiple top-ups, login-required POST.

This is the next Phase 6 step after phone-verify. **Still ungated
in the operator's existing `.env`** — Stripe API keys exist; the
console URL just gets replaced with a real `checkout.stripe.com`
URL once the operator confirms keys are live-mode-correct. Webhook
handler is the next piece (will need `STRIPE_WEBHOOK_SECRET` for
signature verification — that env var is NOT yet set).

### Phase 6 phone-verify endpoints

`POST /auth/phone/start` + `POST /auth/phone/check` wired against the
existing `SmsVerifier` Protocol scaffold. ConsoleSmsVerifier auto-
selected when Twilio env vars are absent (`_dev_code_for` returns the
last 6 phone digits, so tests can predict the OTP without capturing
stderr). E.164 normalisation (`+CC…`, 8-15 digits) on /start; 60-sec
re-send cooldown via `phone_verification_sent_at`; explicit 409 when
already verified; per-attempt audit events (`auth.phone_start`,
`auth.phone_verified`, `auth.phone_check_failed`).

9 new integration tests in `tests/integration/test_phone_verify.py`
covering: stamp-on-start, E.164 rejection, rate limit, cooldown
release, success path with dev code, wrong-code 400 + audit,
check-before-start 409, already-verified 409, CSRF required, login
required.

This is the next named Phase 6 wiring step after Settings (per
HANDOFF "Next session §2"). Operator-side: still needs
`TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_VERIFY_SERVICE_SID`
in `.env` to swap from console → real Twilio Verify; no code change
needed once those are set.

### Pre-push audit script — self-hit fix

`scripts/audit_secrets.py` was hitting its own pattern table (lines
40, 47–51) on every full-tree run, so the operator's pre-push checklist
(`python scripts/audit_secrets.py`) always failed unless invoked with
`--staged-only`. Added a `DETECTOR_FILES = {"scripts/audit_secrets.py"}`
exemption — mirrors the playspace-wide script's already-proven pattern
at `random/scripts/audit_secrets.py:73-77`. Full-tree audit now exits
0 on a clean tree.

### Verification

In the magician sandbox: `python3 -m py_compile` clean on all changed
files; `mypy --strict` clean on the new utils module + unit test
(matches handoff's pattern that `src/app/web/*` is covered by the
module-level mypy override for FastAPI `Request` typing). **Not yet
verified in dev env:** full `mypy --strict src/` + `pytest tests/`
+ live browser E2E. Operator's pre-push checklist still applies.

### Files touched

- `src/utils/kod_urzedu.py` (new)
- `src/app/templates/app/settings.html` (new)
- `src/app/templates/app/_layout.html` (+1 line nav)
- `src/app/web/routes.py` (+3 import lines, +~110 lines routes/validator)
- `src/app/api/invoices.py` (1-line redis.aclose)
- `tests/unit/test_kod_urzedu.py` (new)
- `tests/integration/test_settings.py` (new)

## 2026-05-13 — Polonization: app routes + landing page polish

Operator woke up, gave one directive: "work on urls and landing page
and wording, can't provide .env yet". Two parallel lanes shipped.

### URLs — Polish slugs for user-facing app routes

Convention: user-facing app routes are now Polish (this is a
Polish-first product; URLs are UX). Tech routes (`/api/v1/*`,
`/auth/*`, `/health`, `/webhooks/*`) stay English.

| Before | After |
|---|---|
| `GET /app/upload` | `GET /app/wgraj` |
| `POST /app/upload` | `POST /app/wgraj` |
| `GET /app/invoices` | `GET /app/faktury` |
| `GET /app/invoices/{id}` | `GET /app/faktury/{id}` |
| `POST /app/invoices/{id}/corrections` | `POST /app/faktury/{id}/popraw` |
| `POST /app/invoices/{id}/reextract` | `POST /app/faktury/{id}/ponow-ekstrakcje` |
| `GET /app/invoices/{id}/pdf` | `GET /app/faktury/{id}/pdf` |
| `GET /app/invoices/{id}/export/{fmt}` | `GET /app/faktury/{id}/eksport/{fmt}` |

Touched: `src/app/web/routes.py` (route decorators + every internal
`RedirectResponse(url=…)` + the JSON `redirect` key in corrections
response), all 6 `src/app/templates/app/*.html` files (nav, form
actions, export anchors, `<embed src>`, JS fetch path, reextract form
action), `tests/integration/test_corrections.py` +
`tests/integration/test_export.py`, SPEC §8 page list, HANDOFF TL;DR.

While renaming I also cleaned 4 stale `InvoiceIDP` titles in templates
that were missed in the original rename:
`app/{dashboard,invoices,upload,invoice_detail_stub}.html`.

### Landing page — second pass polish (delegated to subagent)

Files: `src/app/templates/{base,index}.html`. Changes:

- **SEO + Open Graph + favicon** in base.html — Polish meta
  description, keywords, full OG block (`og:title/description/type/
  url/site_name/locale=pl_PL`), Twitter card, inline data-URI SVG
  favicon with the violet "F" mark.
- **Navbar** — added a tiny `Beta v0.1` pill next to the wordmark;
  trimmed anchor row to the 4 most important (`#dla-kogo`,
  `#jak-to-dziala`, `#cennik`, `#faq`).
- **Hero** — replaced the price-pill badge with a positioning badge
  (`Polski produkt · zbudowany dla księgowych`). Rewrote the subhead
  with a "30 sekund" speed signal. Added a friction-killing line under
  the CTAs: *Bez karty na start. Zacznij od jednej faktury — zobacz
  wynik, dopiero potem doładuj.*
- **NEW `#dla-kogo` section** between hero and "Dlaczego" — three
  quieter cards (Biuro rachunkowe / Solo-księgowa / Mała firma), each
  with a Lucide-style inline SVG, pain line, and a bolded `Zysk:`
  benefit line.
- **NEW `#faq` section** between pricing and trust band — 6
  collapsible questions using native `<details>`/`<summary>`, no JS:
  MF gateway acceptance, KSeF/FA(3) vs JPK_FA(4) distinction, EU data
  residency, AI error handling, RODO/DPA, balance runout flow.
- **Pricing fix** — "5 USD" minimum top-up corrected to **20 PLN**
  (`(40 faktur)` parenthetical for quantification). SMS line swapped
  for "Płatność Stripe — karta lub BLIK". Why-card #4 also updated
  (was still 5 USD).
- **Trust band** — now 4 columns on desktop. New "Dokumenty" column
  with placeholder links to `/regulamin`, `/polityka-prywatnosci`,
  `/dpa`, `/status` (HTML comment TODO marks that these FastAPI routes
  don't exist yet).
- **Slim footer** — added `Made in Poland 🇵🇱` and a `Status` link.

Calls worth a second look (operator can revert):
1. Hero badge: positioning over price — revertible.
2. KSeF "planowane po V1" — slight commitment; tone to `rozważamy` if
   you want more wiggle.
3. `Zysk:` prefix on persona cards — salesy-ish; could become `Efekt:`
   or drop entirely.
4. Flag emoji renders inconsistently across font stacks — swap to
   text "PL" if it looks off in your screenshot.

### Verified

- `mypy --strict`: 0 errors in 44 source files.
- `pytest tests/unit/ tests/test_no_data_leak.py`: 53/53 green.
- `scripts/audit_secrets.py`: clean.
- Integration tests (`tests/integration/test_corrections.py`,
  `test_export.py`) updated with new URLs — not executed (Docker
  daemon still down).

### Deferred

- `/regulamin`, `/polityka-prywatnosci`, `/dpa`, `/status` routes
  don't exist. Footer links 404 until the operator (or a follow-up
  session) creates stub pages or wires to external legal templates.
- 301 redirects from the old English routes — not added. There are no
  external bookmarks to break (beta, single-digit users); clean cut.

## 2026-05-13 — Magician overnight: rename to Faktomat, JPK_FA(4) ships, billing scaffold

Operator went to sleep with three answers: VAT yes, JDG yes, product
name "your call, have fun with it". Plus a directive: "make the
website spark". This commit delivers all four lanes.

### Added — product identity

- **Faktomat** = faktura + automat. Tagline: *"Wsadź fakturę, wyjmij JPK."*
- Renamed `InvoiceIDP` → `Faktomat` across `base.html`,
  `app/_layout.html`, `app/invoice_review.html`, `auth/routes.py` email
  subjects, and `pyproject.toml` (`name = "faktomat"`).
- §17 trademark check (`ewyszukiwarka.pue.uprp.gov.pl`) still pending —
  the name should not be printed on physical materials until cleared.
  For beta / web launch the working name is fine.

### Added — landing page spark (delegated to subagent)

- `src/app/templates/base.html` — Inter font (Google), tailwind config
  with `brand` palette (violet 50–900), 4 hero keyframes, `hero-wash`
  radial-gradient CSS class.
- `src/app/templates/index.html` — full overhaul: sticky translucent
  navbar, hero with animated PDF→XML visual, "Why Faktomat" 4-card
  strip, "Jak to działa" 3-step strip, beta pricing card, trust band,
  footer with `v0.1 beta` chip. All Polish copy, mobile-responsive,
  zero JS deps added.
- Placeholder `kontakt@faktomat.pl` is not yet a real address — TODO
  comment in the template marks this.

### Added — Phase 5: JPK_FA(4) XML export (the differentiator)

- **`src/pipeline/export/jpk_fa.py`** — `build_jpk_fa(invoice, …)`
  returns the `<JPK>` lxml element; `to_bytes(...)` wraps it as
  pretty-printed UTF-8 with XML declaration. Implements the
  Ministerstwo Finansów schema per SPEC §7.1:
  - Namespace: `http://crd.gov.pl/wzor/2022/03/03/11455/`
  - `<Naglowek>`: KodFormularza (JPK_FA / "JPK_FA (4)" / wersjaSchemy "1-0"),
    WariantFormularza=4, DataWytworzeniaJPK, DataOd/DataDo (auto-set to
    the month containing the invoice's issue date), NazwaSystemu=Faktomat,
    CelZlozenia (1 = original, 2 = correction), KodUrzedu (from Org).
  - `<Podmiot1>`: filer is the **seller** (NIP from invoice or Org
    fallback) — sales-side JPK_FA per spec.
  - `<Faktura typ="G">`: KodWaluty, P_1 (issue date), P_2A (number),
    P_3A/3B/3C/3D (buyer + seller name + address), P_4A/4B (seller
    country+NIP), P_5A/5B (buyer country+NIP), P_6 (sale date when ≠
    issue date), P_13_x / P_14_x per VAT rate, P_15 (gross total),
    P_16–P_23 flags (all false in V1), RodzajFaktury (VAT/KOREKTA/POZ).
  - `<FakturaCtrl>` / `<FakturaWiersz>` per line / `<FakturaWierszCtrl>`.
  - `JpkFaExportError` raised when filer NIP or KodUrzedu missing — 422
    at the endpoint with a "go fill in Settings" pointer.
  - `validate_xsd(xml_bytes)` — optional XSD check; no-op when
    `schemas/jpk_fa_v4.xsd` not present.
- **`schemas/README.md` + `schemas/.gitignore`** — XSD is not bundled
  (republished by MF often; operator drops the file in to enable strict
  validation). README documents where to grab it and which namespace
  the builder writes today.
- **`src/app/web/routes.py`** — `_EXPORT_FORMATS` now includes
  `"jpk_fa"`. The export endpoint loads the Org alongside the invoice,
  passes filer data, handles `JpkFaExportError` → 422, sets
  `Content-Type: application/xml`, filename `<invoice_number>.xml`.
- **`src/app/templates/app/invoice_review.html`** — JPK_FA button is
  now the primary CTA in the review header (blue), JSON+CSV are
  secondary. The disabled/Phase-5 title is gone.
- **`mypy.ini`** — added `lxml.*` ignore (no stubs).
- **`pyproject.toml`** — `lxml>=5.0` dep added.

### Added — Phase 6 billing scaffold (protocol layer + DB only)

The next session can wire endpoints in 1-2 hours; the foundations are
now stable enough to bolt onto without flux.

- **`src/app/sms.py`** — `SmsVerifier` Protocol + `ConsoleSmsVerifier`
  (deterministic dev code = last 6 digits of phone, predictable for
  tests) + `TwilioSmsVerifier` (Verify API v2). `get_sms_verifier()`
  picks real when all three Twilio env vars are set, console otherwise.
  Mirrors the Postmark/Emailer pattern beat-for-beat.
- **`src/app/billing/__init__.py` + `stripe_client.py`** —
  `StripeClient` Protocol with `ensure_customer` + `create_topup_session`.
  `ConsoleStripeClient` returns fake IDs and a localhost stub URL with
  per-process customer caching. `RealStripeClient` uses hosted Stripe
  Checkout (mode=payment, single-line "Doładowanie Faktomat" at the
  passed-in `amount_grosze`, idempotency-keyed). PCI scope stays at
  Stripe — accountants see a `checkout.stripe.com` URL.
- **`src/app/config.py`** — new settings: `stripe_secret_key`,
  `stripe_publishable_key`, `stripe_webhook_secret`,
  `invoice_price_grosze` (default 50 = 0,50 PLN),
  `twilio_account_sid`, `twilio_auth_token`,
  `twilio_verify_service_sid`. All default to empty so dev / tests run
  without any keys.
- **`alembic/versions/0004_billing.py`** + model updates:
  - `users.phone_number` (E.164), `phone_verified_at`,
    `phone_verification_sent_at`.
  - `orgs.credit_balance_grosze` (int, default 0, not null).
  - `orgs.stripe_customer_id` gets a unique constraint
    (`uq_orgs_stripe_customer_id`) so the webhook can upsert safely.

### Resolved — SPEC §17 open questions

Per operator: VAT yes, JDG (not sp. z o.o.), product name = Faktomat.
Q1, Q2, Q3 marked resolved in `SPEC.md`.

### Verified

- `mypy --strict`: 0 errors in **44** source files (was 40; +4: JPK_FA
  exporter, SMS verifier, Stripe billing package).
- `pytest tests/unit/ tests/test_no_data_leak.py`: **53/53 green**
  (was 33; +12 JPK_FA, +4 SMS, +4 Stripe).
- `scripts/audit_secrets.py`: clean.
- Integration tests NOT run this session — Docker daemon offline on
  operator machine. Phase 4 chunk 3c + Phase 5 integration tests
  (`tests/integration/test_export.py`) will run when Docker is up.

### Deferred for next session

- Phase 6 endpoints + UI: `POST /auth/phone/start`, `POST /auth/phone/check`,
  `GET /app/billing` page with "Doładuj" button, `POST /api/v1/billing/topup`,
  `POST /webhooks/stripe`. Worker credit debit on extraction completion.
  Upload gate (refuse when `phone_verified_at IS NULL` OR
  `org.credit_balance_grosze ≤ 0`).
- Live trademark check at `ewyszukiwarka.pue.uprp.gov.pl` for "Faktomat".
- Stripe + Twilio sandbox account setup — operator's job (account
  creation, key issuance).
- Domain registration (faktomat.pl / faktomat.app) — operator's call.

## 2026-05-13 — Phase 4 chunk 3c: JSON + CSV exports (Track A revenue path)

Profit-magician session. Unblocks the cheapest path to first paid
invoice per HANDOFF Track A: an accountant can now download a finished
invoice as JSON or CSV from the review page. JPK_FA stays a Phase 5
item (XSD source-of-truth, not a 1-hr job).

### Added — export pipeline

- **`src/pipeline/export/__init__.py`** — package marker.
- **`src/pipeline/export/json_export.py`** — `to_bytes(invoice)` returns
  pretty-printed UTF-8 JSON. Strips extraction telemetry
  (`overall_confidence`, `extraction_warnings`, `source_pdf_id`,
  `extracted_at`, `extracted_model`, `extraction_version`) and every
  per-field `confidence` dict on `Counterparty` / `LineItem`. After
  operator review those scores are stale; downstream accountants don't
  need them.
- **`src/pipeline/export/csv_export.py`** — `to_bytes(invoice)` renders
  one row per `LineItem` with denormalised header (invoice + seller +
  buyer columns repeated). UTF-8-BOM so Excel on Windows picks up the
  encoding; Polish column names. Two-layout / XLSX is V1.1 (SPEC §7.2);
  skipped.

### Added — endpoint

- **`GET /app/invoices/{id}/export/{fmt}`** in `src/app/web/routes.py`.
  `fmt ∈ {json, csv}`. Auth-gated, org-scoped, 409 if status ≠ completed
  or canonical_data missing, 400 on unknown format, 404 on cross-org.
  Re-validates the stored canonical against `CanonicalInvoice` before
  serialising — corrupt JSONB returns 500 with a user-safe message.
  Filename derived from `invoice_number` with non-alnum chars
  conservatively replaced by `_`. Writes `invoice.exported` audit event
  with `{invoice_id, format, size_bytes}` payload so future Phase 6
  billing can reconcile downloads with metered usage.

### Changed — review page

- `src/app/templates/app/invoice_review.html` — JPK_FA button stays
  disabled with `title="Phase 5"`. JSON + CSV buttons are now live
  `<a>` links to the export endpoint. Native browser download — no JS
  needed.

### Tests

- `tests/unit/test_export.py` — 5 tests: JSON strips telemetry, JSON
  strips per-field confidence, JSON preserves canonical fields, CSV
  has BOM + Polish header, CSV one-row-per-line.
- `tests/integration/test_export.py` — 6 tests: JSON happy path with
  audit event, CSV happy path, unknown format → 400, status≠completed
  → 409, cross-org → 404, unauth → 303 to /login. Mirrors the
  `test_corrections.py` fixtures (signup → verify → login).

### Verified

- mypy --strict: 0 errors in 40 source files (was 37; +3 export modules).
- pytest unit + no-data-leak: 33/33 (was 28; +5 export unit tests).
- `scripts/audit_secrets.py`: clean.
- Integration tests not run in this session — Docker daemon not running
  locally. Wiring mirrors `test_corrections.py` (same fixtures, same
  auth flow) and the same `Response` / `Depends(get_session)` patterns
  used elsewhere; run with `docker compose up -d` before push.

### Why this and not Phase 6 billing first

HANDOFF Track A flagged JSON export as the unblocker. With it shipped:
- accountants in the closed beta can pull data out of the system, so
  the product becomes usable end-to-end (no more "OCR demo" framing);
- the `invoice.exported` audit event will be the data source for
  Phase 6 metered billing without a schema change;
- Phase 5 JPK_FA (the differentiator) is now the *only* gating feature
  on §15, narrowing the next session's scope to one well-defined task
  (download XSD, build lxml element tree, validate, serve).

Phase 6 billing rails are still gated on SPEC §17 decisions
(JDG vs sp. z o.o., VAT registration, product name). Code work can
start once those are settled.

## 2026-05-13 — V1.3 cost-budget pivot (Claude budget seed = $10, bootstrap)

Manager-driven pivot. Constraint: $10 Claude API seed + AWS free tier
for hosting. Revenue from paying beta users refills the cap as it
depletes — bootstrap principle, not permanent austerity.

### Changed — extraction routing

- **`src/pipeline/extraction/extractor.py`** — dropped Sonnet auto-
  fallback in `extract_from_pdf`. Routing is now Haiku-only. The Phase 4
  editable review page is the correction layer for low-confidence
  Haiku output. `extract_from_pdf_force` still exists; the "Re-
  ekstrakcja (Sonnet)" button on the review page is the only path that
  calls Sonnet now. Net cost: ~$0.011–0.025/invoice → **~$0.004/invoice**
  (≈6× reduction).
- Removed unused `HAIKU_CONFIDENCE_THRESHOLD` constant.
- Bumped `EXTRACTION_VERSION` to `v1.1`.
- `src/models/invoice_record.py` — updated `extraction_path` comment to
  reflect the new path values (`haiku-only` / `forced-{model}`).

### Changed — SPEC v1.2 → v1.3

- §1 hosting: Hetzner CPX21 → AWS EC2 t2.micro (free tier Y1; bootstrap,
  reinvest revenue into bigger infra).
- §6 routing: removed two-pass Haiku→Sonnet; Sonnet now manual-only.
- §10.3: phone verification moves from "at 30 invoices" to "before any
  upload" — abuse prevention.
- §11: full hosting/cost rewrite; new bootstrap principle paragraph.
- §14: pricing reframed. Beta is paid-only PAYG (0.50 PLN/invoice,
  $5 min top-up, phone-verified). 3-lifetime-uploads free tier moves
  to V1.4.
- Storage references: `Hetzner Object Storage` → `AWS S3 eu-central-1`
  throughout (§1, §4, §11, §12, §13).

### Changed — PII sanitisation (overdue cleanup from chunk 3a/3b)

- `tests/unit/test_nip.py`, `tests/unit/test_validation.py`,
  `tests/integration/test_corrections.py` — real third-party NIP
  (flagged in `tests/test_no_data_leak.py` forbidden list) replaced
  with synthetic checksum-valid `1234567819` throughout. Function /
  comment names anonymised.
- `CHANGELOG.md` — example invoice line previously containing real
  invoice number + person name (also flagged in the canary list)
  replaced with synthetic `FV/01/2026/001 · Sprzedawca sp. z o.o.`.
- `tests/test_no_data_leak.py` — kept the forbidden-strings canary list
  intact (it's the test of the leak invariant; the strings *must* live
  there). The `audit_secrets.py` allowlist includes this file.
- New `scripts/audit_secrets.py` — pre-push grep for known secret
  patterns + canary PII. Exits 1 on hit. Run before every push.

### Verified

- mypy --strict: 0 errors in 37 source files.
- pytest: 53/53 green.
- No live Claude API calls during this session (no Bedrock spend).

## 2026-05-12 — Phase 0 + Phase 1 prototype

- Bootstrapped `invoice_idp/` per SPEC.md §4 layout
- `scripts/gmail_pull.py` — IMAP attachment fetcher (531 PDFs from 341 messages)
- `scripts/curate_eval_set.py` — filename + first-page-content classifier; 321 noise files moved to `eval_set/_noise/`
- `scripts/dedup_eval_set.py` — sha-1 content dedup; 8 self-forward duplicates removed
- **Phase 0 invoice gathering: 202 candidate faktury in `eval_set/`** (>50 target, ~15 distinct seller layouts)
- **Phase 1 prototype delivered:**
  - `src/models/invoice.py` — Pydantic v2 `CanonicalInvoice` per §5
  - `src/utils/{nip,regon}.py` — checksum validators
  - `src/pipeline/validation/checks.py` — VAT math, totals, currency, NIP/REGON rules
  - `src/pipeline/extraction/{pdf,provider,anthropic_provider,extractor}.py` — PyMuPDF rasterisation + Anthropic tool-use + Haiku→Sonnet routing per §6
  - `prompts/extraction_v1.md`
  - `scripts/extract_prototype.py` — CLI
- 23/23 unit tests green
- Smoke test on `Faktura.pdf` (ebratek): path=haiku-only, conf=0.93, 0 warnings, all critical fields correct

## 2026-05-12 — Phase 1 eval + retrospective (spec v1.2)

- `scripts/run_eval.py` — random-sample eval harness with resume support
- `scripts/spotcheck.py` — operator manual-verdict tool (g/m/b per
  header/totals + notes, saved to `_results/_verdicts.json`)
- Eval on 20-PDF sample: 18/20 ok after fixes (retry bump from 2→5,
  `<UNKNOWN>` → null normaliser, money-amount rounding to 2dp)
- Spotcheck 17/18 (VTIT skipped as account statement); after moving
  4 out-of-scope docs to `_noise/` (refund notice, account statement,
  shady marketplace receipt, French TVA seller), 13 in-scope faktury
  reviewed:
  - Header good: 11/13 (84.6%) — just under §15 85% gate
  - Totals good: 11/13 (84.6%) — just under §15 90% gate
  - 1 real extraction issue (ganjafarmer — VAT-inclusive products
    with shipping-only VAT, schema gap, now documented in §16)
  - 1 schema-fit issue (alsachim — quote/offer mapped to PROFORMA,
    now documented in §16)
- Haiku-first routing hit rate: 44.4% — below §14 60% gate
- **Spec bumped to v1.2:**
  - §6 routing: dropped explicit "OR hard errors" trigger; rely on
    confidence-threshold-plus-penalty mechanic
  - §15 Phase 1 done-when: accuracy gate explicitly scoped to
    in-scope documents only (out-of-scope per §16 excluded)
  - §16: two new bullets for VAT-inclusive-products receipts and
    OFFER→PROFORMA mapping
- `extract_from_pdf` routing updated to match v1.2 §6
- `scripts/curate_eval_set.py` patterns extended: `potwierdzenie_*`,
  `notifications_`, `buyer_advice`, `zestawienie_operacji`, `wyciag`
- Phase 1 closed. Moving to Phase 2.

## 2026-05-12 — Phase 2 infrastructure (chunk 1: scaffold + models)

- `docker-compose.yml` — Postgres 15-alpine with healthcheck (Phase 2 dev DB)
- `src/app/{config,db,main}.py` — Pydantic-Settings env loader,
  SQLAlchemy 2.x async engine + `Base` + `get_session` dependency,
  FastAPI app with `/health` endpoint
- `src/models/{org,user,usage,audit}.py` — SQLAlchemy 2.x models with
  `Mapped[...]` typed columns, soft-delete columns, JSONB on audit
- `alembic.ini` + `alembic/env.py` (async-aware) + `alembic/script.py.mako`
  + `alembic/versions/0001_baseline.py` baseline migration
- `.env` extended: `DATABASE_URL`, `SESSION_SECRET`, `CSRF_SECRET`
- New deps: fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg,
  alembic, argon2-cffi, itsdangerous, pydantic-settings, jinja2,
  python-multipart, httpx, pytest-asyncio
- Smoke: imports clean, 4 tables registered, 23/23 Phase 1 tests green

## 2026-05-12 — Phase 2 auth flow (chunk 3+4: signup/login/reset + CI)

- `src/app/email.py` — `Emailer` Protocol with `ConsoleEmailer` (dev
  fallback) and `PostmarkEmailer`. Auto-selected by `get_emailer()`:
  Postmark when `POSTMARK_API_TOKEN` + `POSTMARK_FROM_EMAIL` set, else
  console. Email-send failures are caught + logged, never break the
  request flow.
- `src/app/auth/passwords.py` — argon2id wrapper (argon2-cffi).
- `src/app/auth/csrf.py` — synchronizer-token CSRF (session-stored,
  echoed via `X-CSRF-Token`).
- `src/app/auth/deps.py` — `get_current_user`, `require_verified_email`
  FastAPI dependencies.
- `src/app/auth/routes.py` — JSON-API endpoints: `/auth/signup`,
  `/auth/verify-email`, `/auth/login`, `/auth/logout` (CSRF),
  `/auth/forgot-password`, `/auth/reset-password`, `/auth/me`,
  `/auth/csrf`. All side-effects audit-logged. forgot-password
  responds identically whether the email exists or not (no enumeration).
- `src/app/main.py` — wired SessionMiddleware (30-day cookie,
  same_site=lax, https_only off in debug) + auth router.
- `tests/integration/conftest.py` — fixtures: WindowsSelectorEventLoop
  policy fix, alembic upgrade on test DB, per-test TRUNCATE, async
  httpx client. Session-scoped event loop avoids asyncpg/proactor
  reuse across tests.
- `tests/integration/test_auth.py` — 8 tests covering happy paths +
  duplicate signup + password validation + wrong password + bad
  verify token + password-reset flow + email-enumeration mitigation.
- `.github/workflows/ci.yml` — Postgres service, runs pytest + mypy --strict.
- pyproject extended: `asyncio_default_*_loop_scope = "session"`.
- mypy.ini per-module overrides: `src.app.auth.*` waives `type-arg`
  (FastAPI rejects parameterised `Request[Any]` at route-binding time);
  `src.pipeline.extraction.{pdf,anthropic_provider}` waive untyped-call
  / call-overload for the unstubbed third-party libs.

**Phase 2 done-when (§15):** all checked.
- 31/31 tests green (23 Phase 1 unit + 8 Phase 2 integration)
- mypy --strict clean (28 source files)
- CI workflow committed

## 2026-05-13 — Phase 3 chunk 1: Bedrock switch (§17 decision 8)

- `pip install anthropic[bedrock]` adds boto3 dep
- `anthropic_provider.py` refactored: shared `_call_claude()` helper
  (Anthropic + AnthropicBedrock clients have identical messages.create surface)
- `bedrock_provider.py` — `BedrockExtractor` using `eu.anthropic.*`
  cross-region inference profile model IDs
- `extractor.get_extractor()` factory: returns BedrockExtractor when
  AWS_* env set, else AnthropicExtractor. Driven entirely by env vars.
- Settings + .env extended with AWS_ACCESS_KEY_ID / _SECRET_ACCESS_KEY / _REGION
- Smoke test against ebratek Faktura.pdf via Bedrock: path=haiku-only,
  conf=0.85 (1 hard warning: BDO read as REGON, fails checksum). v1.2
  §6 routing validated — single warning doesn't force Sonnet, gets
  surfaced to Phase 4 UI instead.
- Prompt tweaked to teach BDO ≠ REGON.

## 2026-05-13 — Phase 3 chunk 2: upload + storage + worker

- `docker-compose.yml` extended with **Redis** (job queue) + **MinIO**
  (S3-compatible storage) + `minio-init` one-shot to create the bucket
- `src/app/storage.py` — `ObjectStorage` boto3 wrapper (put/get/delete/
  presigned/exists/ensure_bucket); `get_storage()` lru-cached factory
- `src/models/invoice_record.py` — SQLAlchemy `Invoice` model (separate
  from the Pydantic `CanonicalInvoice`): status, S3 key, size, sha256,
  canonical_data JSONB, extraction telemetry, soft-delete
- `alembic/versions/0002_invoice.py` migration; applied to dev DB
- `src/app/api/invoices.py` — POST/GET/list endpoints
  (`POST /api/v1/invoices`, `GET /api/v1/invoices`, `GET /api/v1/invoices/{id}`)
  with PDF magic-byte check, size cap, sha256-based idempotency, audit logging
- `src/app/workers/extract.py` — **arq** background worker:
  loads pending invoice → S3 get → `extract_from_pdf` → persists canonical
  JSON + status. Errors caught → status=failed with message
- `src/app/main.py` wired the new router
- `prompts/extraction_v1.md` — note that BDO ≠ REGON
- **`tests/test_no_data_leak.py`** — the `test_lookahead.py` analog:
  prompt has no real-customer data, no secret-shaped tokens, schemas
  preserve VAT/currency enums, Bedrock map stays in EU
- `tests/integration/test_invoices.py` — 7 tests for upload endpoint
  (success, magic-bytes, empty file, auth required, email verification
  required, sha256 idempotency, cross-org isolation), storage + arq
  monkey-patched

**Phase 3 done-when (§15):** all checked.
- 44/44 tests green (23 unit + 5 no-data-leak + 8 auth + 8 invoice)
- mypy --strict clean (35 source files)
- Full pipeline: web upload → S3 store → arq job → worker → CanonicalInvoice → DB persist

## Dev runbook (Phase 3)

```bash
# 1. Start all infra
docker compose up -d
# 2. Apply DB migrations
alembic upgrade head
# 3. Web app (terminal 1)
uvicorn src.app.main:app --reload --port 8000
# 4. Worker (terminal 2)
python -m arq src.app.workers.extract.WorkerSettings
# 5. MinIO console:  http://localhost:9001  (minioadmin / minioadmin)
# 6. App OpenAPI:     http://localhost:8000/docs
```

## 2026-05-13 — Phase 4 chunk 1: web UI auth flow

- `src/app/templates/` — Jinja2 templates with **Tailwind via CDN** (no
  build pipeline), HTMX 2.0 preloaded for later interactive bits
- Templates: `base.html`, `index.html` (landing), `auth/{login,signup,
  signup_done,verify_result,forgot,reset}.html`, `app/{_layout,
  dashboard}.html` — all Polish UI strings
- `src/app/web/routes.py` — web (HTML) router with form-submit handlers
  mirroring the JSON auth router. CSRF on `/logout` via hidden form
  field, validated against same session-stored token.
- `src/app/main.py` — wired Jinja templates + web router
- `src/app/config.py` — added `session_cookie_secure` (default False
  so http://localhost works; prod sets True via env). De-conflated
  from `debug` flag.
- Tests' conftest no longer needs `DEBUG=true` hack — session cookies
  work on http:// in tests by default.
- Smoke (uvicorn local): GET / 200, /login 200, /signup 200,
  /forgot-password 200, /app unauth → 303 → /login. Full browser flow
  signup → verify → login → /app renders dashboard.html correctly.
- mypy.ini extended: `src.app.web.*` waives `type-arg` (same FastAPI
  Request constraint as auth routes).
- mypy --strict: 37 source files clean; pytest: 44/44 green.

**Phase 4 chunk 1 done.** Remaining for Phase 4: upload page, invoice
list page, **invoice review page** (PDF.js + editable fields, the
critical UX), settings, Playwright E2E.

## 2026-05-13 — Phase 4 chunk 2: upload + invoices list

- `src/app/templates/app/upload.html` — drag-drop zone with HTML5
  file input + JS fallback, native browser drag-drop visual feedback
- `src/app/templates/app/invoices.html` — paginated table list with
  status pills (Gotowe / Przetwarzam… / W kolejce / Błąd), confidence
  pills, warning count
- `src/app/templates/app/invoice_detail_stub.html` — auto-refreshing
  page for `pending` / `processing` invoices (3s reload)
- web router: `GET/POST /app/upload`, `GET /app/invoices`,
  `GET /app/invoices/{id}` (renders stub or review based on status).
  Upload reuses sha256-idempotency from the API; redirects to detail.

## 2026-05-13 — Phase 4 chunk 3a: invoice review page (read-only)

- `src/app/templates/app/invoice_review.html` — split-screen layout:
  PDF inline via `<embed>` left, structured fields right
- Sections: header, seller, buyer, lines table, VAT summary table,
  totals, payment, notes, meta. Confidence pills per field where
  self-confidence is reported. Validation warnings prominently above.
  Polish UI throughout.
- `GET /app/invoices/{id}/pdf` — server-side proxy from MinIO to
  browser (keeps S3 credentials private; CORS-free for embed)
- Export buttons + edit/re-extract buttons stubbed for chunks 3b/3c.

## 2026-05-13 — Phase 4 chunk 3b: editable review page + corrections + re-extract

- **`alembic/versions/0003_invoice_review.py`** — adds two timestamp
  columns to `invoices`: `user_reviewed_at` (stamped on first save) and
  `last_correction_at` (updated on every save). The pair lets Phase 5
  exports gate on "operator has signed off" without inventing a separate
  state machine.
- **`src/pipeline/extraction/extractor.py`** — new
  `extract_from_pdf_force(pdf_path, extractor, model)` runs a single
  model bypassing Haiku→Sonnet routing. Powers the re-extract button.
- **`src/app/workers/extract.py`** — `extract_invoice_task` now takes
  an optional `force_model` arg. When set, calls
  `extract_from_pdf_force`; also resets `user_reviewed_at` +
  `last_correction_at` so a re-extract correctly wipes prior review
  (the data the operator signed off on is gone).
- **`src/app/api/invoices.py:_enqueue_extraction`** — gains an optional
  `force_model` parameter forwarded as the second arq job arg.
- **`src/app/web/routes.py`** — two new endpoints, both CSRF-protected:
  - `POST /app/invoices/{id}/corrections` — accepts a single form field
    `canonical_json` carrying the JSON-serialised partial
    `CanonicalInvoice` (only fields in `_EDITABLE_FIELDS`). Merges over
    the existing `canonical_data` (metadata pinned from prior), re-runs
    Pydantic + `validate()`, persists, stamps the review timestamps,
    writes `invoice.corrected` audit event. Returns JSON envelope so the
    page's fetch() submit can show inline errors (200 ok / 400 bad JSON
    / 422 schema errors / 403 csrf / 404 wrong-org).
  - `POST /app/invoices/{id}/reextract` — flips status back to pending
    and enqueues with `force_model=SONNET_MODEL`. Refuses if the row is
    already pending/processing. Writes `invoice.reextract_requested`
    audit event.
- **`src/app/templates/app/invoice_review.html`** — full rewrite from
  read-only to fully editable. Every field is a live `<input>` /
  `<select>` / `<textarea>`. The right panel is a single form; JS on
  submit walks the named inputs into a `CanonicalInvoice`-shaped dict,
  stuffs it into a hidden `canonical_json` field, POSTs via fetch.
  Layout: header (number/type/dates) → seller+buyer side-by-side →
  lines table (delete-per-row supported, add-row not yet) → vat_summary
  table (delete-per-row) → totals → payment → notes. Invoice-level
  currency dropdown applies to every Money on save (mixed-currency
  invoices are a hard-warning case anyway).
- **Keyboard shortcuts** per spec §9: `Ctrl+S` / `⌘S` saves, `?` opens
  shortcut dialog, `Esc` closes it. Tab/Shift+Tab work natively. The
  shortcut hints render as `<kbd>` blocks.
- **"✓ przejrzane" badge** at top of review page once `user_reviewed_at`
  is set — supersedes the per-field confidence pills (which become
  stale after operator edits).
- **Tests:** `tests/integration/test_corrections.py` — 9 new tests
  covering happy save path, metadata-overwrite rejection, warning
  refresh after fix, 422 on schema fail, 400 on bad JSON, CSRF guard,
  cross-org 404, re-extract enqueue with Sonnet + status reset,
  re-extract skip when already processing. Total: 53/53 green
  (was 44/44; +9).
- mypy --strict: 37 files, 0 errors.

**Phase 4 chunk 3b done.** Remaining for Phase 4: chunk 3c (export
buttons → JPK_FA / JSON / CSV — overlaps with Phase 5), chunk 4
(settings page), chunk 5 (Playwright E2E).

**Stretch left in 3b for a follow-up:** add-row UI for lines /
vat_summary (currently only delete supported); HTMX-style per-field
inline patches (currently bulk JSON post).

## 2026-05-13 — Two roadblocks fixed during E2E verification

### Roadblock 1: worker missing FK metadata

Worker process imported only `Invoice` + `AuditEvent`. `Invoice.org_id`
FK to `orgs.id` couldn't resolve at flush time because `Org` table
wasn't in `Base.metadata`. Failed with
`NoReferencedTableError: ... 'orgs' with which to generate a foreign key`.

**Fix:** `src/models/__init__.py` now imports every model module
(`audit, invoice_record, org, usage, user`). Any process touching one
model gets full metadata transitively — web, worker, alembic, ad-hoc.

### Roadblock 2: Bedrock creds not in worker env

`pydantic-settings` loads `.env` into `Settings`, but boto3's default
credential chain reads `os.environ` directly. Worker had Settings
populated but `os.environ` empty → AnthropicBedrock failed with
`could not resolve credentials from session`.

**Fix:** `BedrockExtractor.__init__` now accepts `aws_access_key` /
`aws_secret_key` and passes them to `anthropic.AnthropicBedrock`
explicitly. `get_extractor()` reads from `Settings` and forwards.
No reliance on env-var pollution.

### Full pipeline verified end-to-end

```
browser /signup → /verify-email → /login → /app/upload (Faktura.pdf)
                      ↓
        POST /app/upload → MinIO PUT → DB row (status=pending) → arq enqueue
                                                                      ↓
                                                            arq worker picks up
                                                                      ↓
                                                   MinIO GET → PyMuPDF → Bedrock Haiku
                                                                      ↓
                                                       Pydantic validate → DB UPDATE
                                                       (status=completed, conf=0.929,
                                                        path=haiku-only)
                                                                      ↓
        browser /app/invoices/{id} → renders invoice_review.html
        (FV/01/2026/001 · Sprzedawca sp. z o.o. · 84.13 PLN · Example Org)
```

mypy --strict: 37 files clean; pytest: 44/44 green.
