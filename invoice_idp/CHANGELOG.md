# Changelog

## 2026-05-19 — pivot: integration-first positioning + FA(3) exporter, signup hidden

Re-angle of the Faktomat landing from "JPK_FA for accountants" to
"PDF → import format for your sales / purchase / warehouse system",
plus the FA(3) (KSeF) exporter that makes the integration angle real.
Signup CTAs hidden from the landing while we extend product coverage
— zero customers today, so nothing to deprecate.

### Why

Operator's read on the market: small accountants and solo-księgowi
already own their KSeF tooling (Optima, Symfonia bundles). The
underserved buyer is the **company running an ERP / warehouse / sales
system** that receives supplier PDFs and currently retypes them. FA(3)
is the KSeF e-invoice format most Polish ERPs read natively, so a PDF
→ FA(3) bridge slots directly into their existing import flow.

### What changed

- **`src/pipeline/export/fa3.py` (new, +250 LoC)**: builds the FA(3)
  XML tree from `CanonicalInvoice`. Podmiot1 = original seller from
  the PDF, Podmiot2 = original buyer — purely a transformation of the
  source document, no org settings required (unlike JPK_FA which
  needs org NIP + kod_urzedu). Pre-flight check refuses without
  seller NIP / name / lines / VAT summary. Same XSD validation hook
  pattern as `jpk_fa.py` — drop `schemas/fa3_v3.xsd` to enable
  strict validation.
- **`src/app/web/routes.py`**: `_EXPORT_FORMATS` now includes `fa3`,
  with a new branch in `export_invoice` that converts
  `Fa3ExportError` → HTTP 422 mirroring the JPK_FA pattern. Filename
  suffix `.fa3.xml` distinguishes it from JPK_FA's `.xml`.
- **`src/app/templates/app/invoice_review.html`**: FA(3) is now the
  primary (brand-coloured) export button; JPK_FA / JSON / CSV demoted
  to secondary. Reflects the new positioning: most users want FA(3)
  for ERP import, JPK_FA is the smaller compliance-filing case.
- **`src/app/templates/index.html` — landing pass**:
  - Hero H1: "Wrzuć fakturę, pobierz JPK" → "Wrzuć fakturę PDF,
    wczytaj w systemie". Subhead promotes sprzedaż/zakup/magazyn
    integration as primary value; JPK_FA(4) + FA(3) as supporting.
  - Hero visual: PDF → multi-badge XML card (JPK_FA(4) / FA(3) /
    JSON / CSV) instead of just JPK_FA.
  - Personas: dropped "Biuro rachunkowe" + "Solo-księgowa". New
    trio: Firma handlowa / dystrybutor, Producent / firma usługowa,
    E-commerce / hurtownia.
  - "Dlaczego Faktomat" card 1: "JPK_FA(4) natywnie" → "Wiele
    formatów wyjścia" with inline format badges.
  - "Jak to działa" step 3: "Pobierz JPK_FA" → "Wczytaj w swoim
    systemie".
  - FAQ Q2 (KSeF/FA(3)) rewritten from "planujemy" to "tak, oba" —
    we now do FA(3) generation.
  - New "Integracje" section (added earlier same day) with 12
    example ERP / warehouse / sales programs, plus FAQ Q6a covering
    the same.
  - **Signup CTAs hidden**: navbar "Załóż konto", hero primary CTA,
    pricing "Zacznij teraz", footer "Załóż konto" all replaced with
    `mailto:kontakt@ewwesolutions.work?subject=...wczesny+dostęp`.
    Login link also removed from chrome (operator can still hit
    `/login` directly). Routes themselves still alive.
- **Tests**:
  - `tests/unit/test_fa3_export.py` (new, 9 tests): namespace + form
    metadata, Podmiot1/Podmiot2 mapping, Fa section totals, per-rate
    P_13/P_14 emission, ZW omits P_14, correction → RodzajFaktury=KOR,
    refuses missing seller NIP / no lines, UTF-8 XML declaration.
    All passing local (`.venv/bin/pytest tests/unit/test_fa3_export.py`).
  - `tests/integration/test_export.py`: added `test_export_fa3_happy_path`
    mirroring the JPK_FA happy-path. Asserts 200, `application/xml`,
    `.fa3.xml` content-disposition, `<Faktura>` root, `FA (3)`
    kodSystemowy, `WariantFormularza=3`, both `Podmiot1` and
    `Podmiot2` present. Requires Docker stack — runs on the
    operator's pre-push E2E.

### Caveats — don't re-hit

- **FA(3) XSD not bundled.** The exporter produces structurally
  credible FA(3) XML based on the MF publication shape, but strict
  XSD validation is currently a no-op. Operator should download
  `Schemat_FA(3).xsd` (gov.pl/web/kas) and drop it as
  `schemas/fa3_v3.xsd` before the first paying customer relies on
  KSeF acceptance. Namespace constant `FA3_NAMESPACE` in
  `fa3.py` may need updating if the operator-downloaded XSD's
  `targetNamespace` differs from `http://crd.gov.pl/wzor/2025/06/25/13775/`.
- **Suffix `.fa3.xml`** in download filename is deliberate — gives
  the user a clear cue this isn't JPK_FA. Don't "normalise" to
  `.xml`; it would collide with the JPK_FA download for the same
  invoice.
- **Signup is hidden, not disabled.** `/signup` and `/login` still
  work via direct URL. Re-exposing on the landing = revert this
  commit's mailto: blocks back to `/signup` hrefs.

### Pre-push checklist (operator)

1. `python invoice_idp/scripts/audit_secrets.py` — exit 0.
2. Docker up: `docker compose -f invoice_idp/docker-compose.vm.yml up -d --build`
   (FA(3) is a template + route change; uvicorn has no reload).
3. Live E2E: pick a completed invoice in your test org, click "Eksport
   FA(3)" — should download an XML file with `<Faktura>` root and
   both Podmiot1 + Podmiot2 sections from the PDF.
4. `pytest tests/` (unit + integration, Docker stack required).

---

## 2026-05-16 → 2026-05-17 (overnight) — test coverage pass: auth + upload + status

Three integration-test additions landing the night of 2026-05-16 →
17 under the `/goal` autonomous protocol (`GOAL.md` at repo root).
Operator asleep; agents fanned out via `Explore` surveys +
`general-purpose` implementation, commits local-only awaiting morning
E2E + push.

### What changed

- **`tests/integration/test_auth.py` (+217 LoC across 6 new tests)**:
  - **Password-reset negative paths** (`b1b24b1`): expired token (force-
    aged 1s into the past), bad token (random bogus value, same response
    shape as `expires_at=None` — no enumeration leak), too-short
    password (7 chars → 422 from `Field(min_length=8)`; 8 chars on the
    same token → 200, proving rejection was on length not token lookup).
  - **Email-verify web route parity** (`3e10b5b`): expired-token
    (force-aged 25h, asserts 200 + Polish error template), missing
    `?token=` (asserts 422 from FastAPI validation, NOT a rendered
    template), happy-path (signup → fetch token from DB → GET
    `/verify-email?token=...` → asserts `email_verified` flipped True
    and token+sent_at cleared).
- **`tests/integration/test_upload_validation.py` (+147 LoC, 2 new tests)**
  (`13b3ac8`):
  - **Web form GET** (`/app/wgraj`): renders with `max_upload_mb`
    threaded into both Polish copy (`maks. N MB`) and JS guard
    (`MAX_UPLOAD_MB = N;`), session cookie present.
  - **Web form POST** (happy path): minimal `%PDF-1.4...` body → 303
    redirect to `/app/faktury/{id}`, exactly one pending `Invoice` row,
    `storage.put` and `_enqueue_extraction` each called once.

### What did NOT change

- The `_gather_status()` pure-unit suite was already comprehensive after
  commit `1ba682c` (all-ok / per-probe-fail / all-three-fail /
  concurrent dispatch / ISO timestamp shape / cache bypass /
  tautological Aplikacja row). The original survey listed it as a gap;
  on re-read it was a duplicate, so no second file written.
- No production code touched. No new dependencies. No alembic.

### Key findings worth knowing

- **Web router has NO prefix.** HTML `/verify-email` and JSON
  `/auth/verify-email` coexist without collision. Web returns **200
  with template** for both expired AND unknown-token; JSON raises 400.
  Missing required `?token=` → 422 from FastAPI validation, NOT a
  rendered template. Now documented in `HANDOFF.md` "Don't re-hit
  these".
- **Test DB postgres is NOT host-exposed.** `docker-compose.vm.yml`
  only binds 5432 on the docker bridge network. Tests from the host
  `invoice_idp/.venv` need the bridge IP. Now documented in HANDOFF
  with the full invocation.
- **Integration tests deadlock under xdist.** Workers concurrently
  TRUNCATE the shared `audit_events` table. Use `-p no:xdist` or
  per-worker DBs. Even serialised, the TRUNCATE-CASCADE fixture
  occasionally flakes; rerun resolves.
- **🚩 Upload form has no CSRF protection.** GET handler generates a
  `csrf_token` and passes it into the template context, but
  `app/upload.html` doesn't render it as a hidden input and
  `upload_submit` doesn't verify it. JSON `/api/v1/invoices` also
  skips CSRF. **Discovered, not introduced** by the new test; flagged
  to operator for security decision (see `docs/overnight-2026-05-16.md`
  "Blocked — operator decision needed"). Not auto-closed.

### Verification

```bash
cd invoice_idp
docker inspect invoice_idp_postgres \
  --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
# 172.18.0.4 tonight; re-derive after compose recreate

TEST_DATABASE_URL="postgresql+asyncpg://invoice_idp:invoice_idp@172.18.0.4:5432/invoice_idp_test" \
  .venv/bin/python -m pytest tests/integration/test_auth.py \
  tests/integration/test_upload_validation.py -v -p no:xdist
```

→ 14/14 in `test_auth.py`, 6/6 in `test_upload_validation.py`,
106/106 in `tests/unit/` (no regression). audit_secrets.py: clean.

### Commits

`b1b24b1` (password-reset), `3e10b5b` (email-verify web), `13b3ac8`
(upload form). HANDOFF + overnight log doc updates at `d0872d2`.

---

## 2026-05-16 — shared upload-validation helpers

Resolved the `routes.py:506` TODO without collapsing the two callers
into one handler. The web `POST /app/wgraj` and the JSON
`POST /api/v1/invoices` had drifted into hand-copied duplicates of
the same three guards (empty body / oversize / not-PDF), each with
its own copy and one with a 413 the other lacked. A future fix to
one branch (e.g. tightening the size cap, adding gzip detection)
would have had to remember to touch both files.

### What changed

- **New module** `src/app/services/__init__.py` +
  `src/app/services/invoice_upload.py`. Exports:
  - `PDF_MAGIC_BYTES` — canonical home for the `%PDF-` literal.
  - `UploadIssue(str, Enum)` — `EMPTY` / `TOO_LARGE` / `NOT_PDF`,
    `str` mix-in so values drop straight into audit-event payloads
    without `.value`.
  - `is_pdf_bytes(content)` — pure first-bytes check.
  - `classify_pdf_upload(content, max_bytes)` — returns the first
    failing guard or `None`. Branch order is locked
    `EMPTY > TOO_LARGE > NOT_PDF` so users see the most actionable
    message (a zero-byte upload is "empty" rather than "not a PDF").
- **`src/app/api/invoices.py`** now imports `PDF_MAGIC_BYTES` from
  the service module and dispatches on `UploadIssue` instead of
  re-running `len(content) == 0` etc. Re-exports `PDF_MAGIC_BYTES`
  via `__all__` so existing test/web imports keep working.
- **`src/app/web/routes.py::upload_submit`** uses the same classifier
  but keeps owning the Polish copy + the unified 400 status code (the
  HTML surface intentionally doesn't 413 the way the JSON one does;
  the upload-form re-render is the same page regardless of why it
  rejected). Dropped the `PDF_MAGIC_BYTES` import — pulls everything
  it needs from the service module now.
- **New unit tests** in `tests/unit/test_invoice_upload_service.py`
  (11 cases): branch-order priority, exact-cap boundary, real PDF /
  JPEG / empty / non-magic bodies, and the `str` mix-in contract on
  `UploadIssue`.

### What did NOT change

- The integration tests in `tests/integration/test_upload_validation.py`
  still cover the full Polish copy + 400 wiring. They pass unchanged
  because the observable behaviour of `/app/wgraj` is identical.
- The JSON API still emits 413 for `TOO_LARGE` and 400 for the other
  two — that asymmetry is now load-bearing for clients that gate on
  status codes, and would be a separate API change to harmonise.
- HANDOFF.md test-count recount: 156 → 167 after the 11 new unit
  tests (HANDOFF previously said "169 expected", which had drifted
  upward of the actual count; corrected both call sites).

### Verification

- `mypy --strict src/` — 0 errors (49 source files, was 47).
- `pytest tests/unit tests/test_no_data_leak.py` — 102 passed
  (was 91; +11 from the new file).
- Integration tests not run in sandbox (docker not available); the
  4 upload-validation integration tests should pass unchanged on
  the VM since `/app/wgraj`'s response codes + Polish copy are
  byte-identical to before.

### What to verify on the VM

After `docker compose -f docker-compose.vm.yml up -d --build`:
1. `curl -F pdf=@/dev/null https://faktomat.ewwesolutions.work/app/wgraj`
   (logged in via cookie) still shows "Plik jest pusty." with a 400.
2. `pytest tests/integration/test_upload_validation.py` still green.

## 2026-05-14 (night, part 3) — public /status page

The landing page footer + the "Dokumenty" section in the trust/security
panel both link to `/status`, alongside `/regulamin`,
`/polityka-prywatnosci`, and `/dpa`. Until now all four returned 404 —
embarrassing on a paid B2B SaaS for Polish accountants where
conservative buyers read those links as a competence signal.

This change closes the `/status` link with a real public page. The
three policy links stay as a known TODO for the operator (legal copy
is not Claude's call to write).

### What changed

- **New route** `GET /status` in `src/app/web/routes.py` (public, no
  auth). Probes three downstream services concurrently with hard
  1.5-second timeouts so a single degraded component cannot hang the
  status page itself:
  - `_probe_db`: opens a short-lived SQLAlchemy `SessionLocal` and
    runs `SELECT 1`. Independent from the request's session so a
    stuck probe doesn't poison the main connection.
  - `_probe_redis`: `redis.asyncio.from_url(settings.redis_url)`
    + `PING`. Closes the client in a `finally` block.
  - `_probe_storage`: `boto3` `head_bucket` against the configured
    MinIO bucket (sync call wrapped in `asyncio.to_thread`).
- **In-process cache** keyed off `time.monotonic()` with a 30-second
  TTL. Repeated `/status` hits within the window reuse the cached
  result rather than hammering Postgres/Redis/MinIO. If traffic ever
  warrants persistence across pods, swap for Redis `SETEX` or a
  static-file generator.
- **New template** `src/app/templates/status.html` extending `base.html`
  with Polish copy: "Wszystko działa" / "Wystąpił problem" headline,
  per-service rows with emerald or rose status dots, footer with
  "Sprawdzono <ts> UTC · odświeżane co 30 sekund".
- **Landing TODO updated** — `src/app/templates/index.html` comment
  near the Dokumenty section drops `/status` from the 404 list and
  notes the live route. The three policy links remain on the TODO.

### What it does NOT change

- **The three policy links.** `/regulamin`, `/polityka-prywatnosci`,
  `/dpa` still 404. Writing legal copy is operator-side work; even a
  "coming soon" stub may set wrong expectations for a paid product.
- **External monitoring.** `/health` (in `main.py`) remains the
  no-auth liveness probe for UptimeRobot and similar — still returns
  bare JSON, still doesn't probe downstreams. `/status` is the
  human-facing complement.
- **`audit_events` writes.** The status page is read-only; it logs
  nothing.

### Tests

`tests/integration/test_status_page.py` — 4 new test functions:

- `test_status_page_all_services_up` — all three probes monkeypatched
  to OK, asserts "Wszystko działa" headline, all four service rows
  present (App + DB + Queue + Storage), no "Problem" badge anywhere.
- `test_status_page_db_down_shows_degraded_state` — DB probe returns
  `{ok: False, error: "ConnectionRefusedError"}`. Asserts the
  "Wystąpił problem" headline replaces the success copy and the error
  class surfaces as the title attribute on the failing row.
- `test_status_page_does_not_require_auth` — `follow_redirects=False`
  catches a stray 303 to `/login`; status code must be 200.
- `test_status_page_caches_within_ttl` — three sequential `/status`
  calls within the 30s window result in exactly one call to each
  probe. Guards against a regression that breaks the cache.

A module-scoped fixture `_clear_status_cache` resets `_STATUS_CACHE`
before each test so the cache from one test doesn't bleed into the
next. mypy --strict clean on `src/app/web/routes.py`. Expected pre-push
test count: 165 → 169.

### Followup

- `HANDOFF.md` test count bump (165 → 169) — done same change.
- Discord ops alerter (a separate item in the CLAUDE.md "Next:" list)
  could read `/status` instead of running its own probes, since both
  surfaces want the same downstream-health view. Out of scope here.

---

## 2026-05-14 (night, part 2) — friendly Polish errors on the failed-invoice page

Pairs with the stub Re-ekstrakcja button shipped earlier in the same
night. Before this change `invoice.extraction_error` held the raw
Python exception (e.g. `BedrockAccessError: model access not granted`,
`ValidationError: 5 validation errors for CanonicalInvoice`,
`ClientError: An error occurred (ThrottlingException) when calling
InvokeModel`) and that string went straight into the red error box on
`invoice_detail_stub.html`. A paying Polish accountant has no reason
to read boto3 internals — and the noisy English class names made the
"Spróbuj ponownie (Sonnet)" button feel like a desperate workaround
rather than the intended recovery action.

### What changed

- **New module** `src/app/workers/error_messages.py` — pure-function
  `friendly_extraction_error(exc)` mapping raw exceptions to Polish,
  action-oriented messages. Matches by class name + message substring,
  most-specific first, with a generic fallback so nothing ever leaks
  a Python traceback.
- **Categories covered**: PDF too many pages (`ValueError` + "pages >"),
  storage endpoint / NoSuchKey, throttling (`ThrottlingException`,
  `RateLimitError`, `OverloadedError`, `TooManyRequestsException`,
  + substring fallback for SDK-wrapped `ClientError`), auth /
  permission (`AccessDeniedException`, `PermissionDeniedError`,
  `AuthenticationError`, `NotAuthorizedException`), backend down
  (`ServiceUnavailableException`, `InternalServerException`,
  `InternalServerError`, `APIConnectionError`), timeouts
  (`TimeoutError`, `APITimeoutError`, `ModelTimeoutException`,
  `ReadTimeoutError`), Pydantic `ValidationError`, RuntimeError "did
  not call" / "failed to parse" (Haiku / Sonnet emit_invoice misses).
- **Worker change** in `src/app/workers/extract.py` — the
  `except Exception` block now writes the friendly message to
  `invoice.extraction_error` (user UI surface) and stashes the raw
  `f"{type(e).__name__}: {e}"[:1024]` into the audit event payload
  under the new `error_raw` key. Existing `error` key in the payload
  keeps the same value as `invoice.extraction_error` (user message),
  so downstream tooling that reads `payload->>'error'` gets the same
  Polish copy that the user sees.

### What it does NOT change

- **Schema.** No migration. `invoice.extraction_error` is still
  `String(1024)`, just now carrying a Polish UTF-8 message instead of
  a Python class name. The new `error_raw` field lives inside the
  JSONB payload — no column added.
- **The daily-digest agent.** `scripts/agents/daily_digest.sh` already
  reads `invoice.extraction_error` directly; it will now show the
  friendly Polish message in the Discord ping. For raw debugging the
  operator queries `audit_events.payload->>'error_raw'` (or reads
  `docker compose logs worker` for the actual stack trace).
- **The retry handler.** `/app/faktury/{id}/ponow-ekstrakcje` clears
  `extraction_error` on entry (`routes.py:711`) so the user message
  disappears as soon as a re-extract is queued.

### Tests

`tests/unit/test_error_messages.py` — **34 new test functions** across
parametrised classes, message-substring fallbacks, Pydantic
`ValidationError`, RuntimeError pipeline failures, and a guarantee
test ensuring no branch ever returns an empty string or leaks the
Python class name. mypy --strict clean on both modified files.
Integration test count unchanged (still 131); unit test count goes up
by 34 → total **165 test functions** expected pre-push.

### Followup

- `HANDOFF.md` "Outstanding cleanups" — bump expected test count to
  165 in both places.
- Consider exposing `audit_events.payload->>'error_raw'` in the daily
  digest so the operator sees both flavours side-by-side. Tabled —
  Discord 2000-char limit and the daily digest is already busy.

---

## 2026-05-14 (night) — Re-ekstrakcja button on failed-invoice detail stub

Recovery action for the failed-extraction case. Before this change a
paying user whose extraction failed (transient Bedrock blip, ambiguous
PDF, model-access error) landed on `invoice_detail_stub.html`, saw the
red `Błąd` badge plus the error message — and had no in-UI path
forward. The "Re-ekstrakcja (Sonnet)" button on the review page
(`invoice_review.html` line 415) only renders for `status='completed'`
invoices, so a failed invoice was a dead end. The only recovery was
to re-upload the same PDF, which costs a second debit.

### What changed

`src/app/templates/app/invoice_detail_stub.html` — added a single
`{% if invoice.status == 'failed' %}` block between the error-message
panel and the (never-rendered-here) `canonical_data` JSON section.
Block contains a tiny `<form method="post"
action="/app/faktury/{{ invoice.id }}/ponow-ekstrakcje">` with the
existing `csrf_token` (already in template context — see
routes.py:540, 548) and a single submit button labelled `Spróbuj
ponownie (Sonnet)`. Helper text spells out that each retry consumes
one debit from the wallet — beta is paid-only PAYG, surprising the
user on a "free recovery" would be a trust hit.

### What it reuses

The existing backend handler at `src/app/web/routes.py:670–723`
(`@router.post("/app/faktury/{invoice_id}/ponow-ekstrakcje")`) already
accepts `status in ('completed', 'failed')` per line 703, hard-codes
`SONNET_MODEL`, sets status back to `pending`, clears
`extraction_error`, and audit-logs `invoice.reextract_requested`. No
backend changes, no migration, no new route, no JS, no new template.
The same `csrf_token` template variable that the review-page form
uses (`invoice_review.html:431` form action) is already passed to the
stub by `invoice_detail` (routes.py:540–548) — verified before edit.

### What's intentionally not in scope

- **No confirm dialog.** The review-page button warns "wyzeruje status
  przeglądu" because a `completed` invoice may have operator
  corrections to lose. A `failed` invoice has none —
  `user_reviewed_at` / `last_correction_at` are null — so the click
  is already the deliberate action.
- **No list-page per-row re-extract action.** Each row in
  `invoices.html` already links to the stub, which is now the
  recovery surface. Adding a duplicate entry point on the cramped row
  layout would double the surface area without serving a new use
  case. Revisit if failure-rate goes >5% per session.
- **No model choice in UI.** Backend hard-codes Sonnet; SPEC §6
  cost-budget pivot keeps this as the manual escalation tier.

### Verification path (operator, pre-push)

1. Static: `grep -n 'ponow-ekstrakcje'
   invoice_idp/src/app/templates/app/invoice_detail_stub.html` —
   confirms form action present.
2. Rebuild on VM: `cd ~/playspace/random/invoice_idp && docker compose
   -f docker-compose.vm.yml up -d --build` (no `--reload`; template
   changes need rebuild).
3. Synthesise a failed invoice without burning Bedrock budget:
   `docker compose -f docker-compose.vm.yml exec postgres psql -U
   invoice_idp -d invoice_idp -c "UPDATE invoices SET
   status='failed', extraction_error='QA — test failure for re-extract
   button' WHERE id = '<some-recent-uuid>';"`
4. Visit `https://faktomat.ewwesolutions.work/app/faktury/<that-uuid>`
   — expect red `Błąd` badge + red error block + new stone-50 panel
   with `Spróbuj ponownie (Sonnet)` button. Click → 303 to same URL →
   stub now shows the auto-refresh banner (status now `pending`).
5. No-regression on completed invoices: visit a known
   `status='completed'` invoice → review page renders unchanged with
   its own Re-ekstrakcja (Sonnet) button at line 415.
6. Audit row: `SELECT id, action, payload FROM audit_events WHERE
   action='invoice.reextract_requested' ORDER BY created_at DESC LIMIT
   3;` — should include the QA-test invoice id.

### Followup

- `HANDOFF.md` "Next" list — drop the `Re-ekstrakcja button for
  failed-status invoices` line (CLAUDE.md tracks the same item; it
  also gets removed there once HANDOFF clears).
- `CLAUDE.md` table cell for `invoice_idp/` — adjust the "Next:"
  enumeration so it no longer lists this item.

---

## 2026-05-14 (evening) — VM migration: faktomat off Windows host

Customer-facing services moved off the daily-driver Windows machine
into a Hyper-V Ubuntu 24.04 VM (`claude-sandbox`, Default Switch IP
`172.27.83.217`). Motivation: an RCE in faktomat or fiszkomat shouldn't
be able to reach the operator's banking / passwords / other projects;
Windows Updates shouldn't take production down.

### Topology change

Before (hybrid on Windows host): postgres + redis + minio + arq worker
in containers; **FastAPI uvicorn ran directly on the Windows host**
with `--reload`; cloudflared as a Windows-host foreground process,
tunnel → `localhost:8000`.

After (all-in-VM): **everything** in `docker-compose.vm.yml` — postgres,
redis, minio, app (uvicorn, no `--reload`), arq worker. cloudflared
runs as a systemd unit *inside* the VM and talks to upstream over
loopback — no Hyper-V virtual switch in the request path, no 502s
from Default Switch flakiness.

### What landed in repo

- `docs/vm-migration-2026-05-14.md` — full runbook (architecture,
  day-2 ops, issues encountered, rollback procedure, checkpoint list)
- `scripts/create_claude_vm.ps1` — host-side Hyper-V Gen2 VM creation
- `scripts/bootstrap_claude_vm.sh` — guest-side Docker + Node +
  gh + Claude Code + Playwright Chromium + Tailscale setup
- `invoice_idp/docker-compose.vm.yml` — VM compose file (no Caddy;
  cloudflared fronts the request path on loopback)
- `invoice_idp/pyproject.toml` — added `email-validator>=2.0` to main
  deps (Pydantic v2 `EmailStr` requirement). VM Dockerfile briefly
  patched in-place with `pip install email-validator`; upstream fix
  in commit 53f172e reverts the patch so fresh builds work clean.

### Notable issues debugged during migration

(detailed in `docs/vm-migration-2026-05-14.md` §Issues encountered)

- PowerShell 5.1's `>` redirection writes UTF-16 LE with BOM —
  silently corrupts pg_dump COPY-format output. Surfaced via
  `--column-inserts` re-dump; fixed via `iconv -f UTF-16 -t UTF-8` on
  the VM side.
- Hyper-V Default Switch flakiness for sustained cross-machine HTTP
  caused persistent 502s while cloudflared was still Windows-side
  routing to VM:8000. Resolved by moving cloudflared into the VM so
  the request path becomes loopback.
- `cloudflared service install` on Windows (v2025.8.1) registered the
  binPath with no args → cloudflared printed help and exited. Worked
  around with a foreground PowerShell process during cutover, then
  moved entirely to VM systemd.
- `invoice_idp` container restart-looped on `ImportError: email-validator`
  on first fresh build inside the VM. Pydantic `EmailStr` needs the
  package which wasn't in `pyproject.toml`. Band-aided locally, fixed
  upstream in 53f172e.

### Rollback insurance

The Windows compose stack (`invoice_idp_postgres`, `_redis`, `_minio`,
`_worker`) stays warm for ~7 days. Decommission target: 2026-05-21
via `docker compose down -v` on Windows once the VM has proven stable.
Hyper-V checkpoint `cloudflared-on-vm-fully-migrated` taken
post-migration as the final-state restore point.

### Followup landed same-day

- 53f172e — `faktomat: add email-validator to deps, drop Dockerfile band-aid`
- 068534e — `infra: track invoice_idp/docker-compose.vm.yml` (was
  referenced in the runbook but missed from the runbook commit)
- VM rebuild after both commits verified: `email_validator 2.3.0`
  importable inside the container without the Dockerfile patch,
  `/health` 200 locally, `faktomat.ewwesolutions.work` 200 from
  internet (~94ms).

---

## 2026-05-14 — Phase 6 LIVE: first end-to-end paying flow worked

Faktomat takes money. The entire chain ran successfully on the live
stack 2026-05-14 evening: new signup → email-verify (Postmark) →
Stripe Checkout top-up → upload PDF → Bedrock Haiku extraction
(eu.anthropic.claude-haiku-4-5) → editable review page → JPK_FA(4)
XML export. The operator's first 20 PLN was charged, credited,
debited at extraction, and the export downloaded as a valid XML.

This entry is the wrap-up that closes Phase 6. See HANDOFF.md
"Suggested next action" for what comes next (stabilisation, not
new feature gates).

### What got debugged live to get here

The sequence of fixes that turned "stack-is-up" into "stack-actually-
processes-paying-invoices":

1. **Upload-gate phone-verify relaxation** (PR #14). `_upload_gate()`
   was checking `phone_verified_at IS NOT NULL`, but Twilio was
   deferred to Phase 7+ — every new user 303'd to `/app?reason=phone`
   with no way through. Changed the gate to `email_verified` +
   positive balance; phone-verify code stays dormant in tree.
2. **`Wgraj i ekstrahuj` → `Wgraj i ekstraktuj`** (PR #16). Single
   PL string fix flagged by operator on live UI.
3. **arq worker in dev compose** (PR #17). The worker was previously
   a `python -m arq` invocation in a manual PowerShell window. When
   that window closed, every new upload stalled in `status=pending`.
   Worker now runs as a compose container with `restart:
   unless-stopped` so it survives reboots and crashes.
4. **Dockerfile copies `prompts/`** (PR #19). Containerised worker
   crashed on first job with `FileNotFoundError:
   /app/prompts/extraction_v1.md` — the prompt template was never
   copied into the image. Host-run worker hadn't noticed because it
   reads files straight off the working tree.
5. **`enqueue.py` utility** (PR #20). Stuck invoices (worker was
   down at upload time → `status=pending`; or transient error →
   `status=failed`) needed an enqueue retry. Operator's first draft
   had paste-buffer indent issues; reflowed as a proper CLI module
   and baked into the Dockerfile so the call shape is just
   `docker compose exec worker python enqueue.py <invoice_id>`.
   `[force_model]` second argument mirrors the "Re-ekstrakcja
   (Sonnet)" UI button.

### Runtime layout (recorded here so the next session has it)

Hybrid: Postgres + Redis + MinIO + arq worker run as compose
containers; the FastAPI app (uvicorn) runs **directly on the host**
with `--reload`. Cloudflare tunnel terminates TLS and routes
`faktomat.ewwesolutions.work` → `localhost:8000`. `docker-compose.
prod.yml` exists in the tree but is NOT what's currently deployed;
it requires `DOMAIN` / `ADMIN_EMAIL` / `POSTGRES_PASSWORD` the
operator hasn't populated. Don't suggest that compose to him.

### Verification

This entry doesn't introduce new code — it documents the closing of
Phase 6. Tests last verified at PR #14 merge: 130 passing,
`mypy --strict` clean. PRs #16-20 are doc + Dockerfile + utility
changes that don't move test counts. `audit_secrets.py` exit 0.

### Lesson recorded to memory

- `feedback_sandbox_env_divergence` — sandbox `.env` ≠ prod
- `project_faktomat_runtime` — the hybrid layout
- `feedback_powershell_python_quoting` — never multi-line `python
  -c` for Windows operator


---

**Older entries (Phase 0 → Phase 6 wiring) moved to
[`CHANGELOG-archive.md`](CHANGELOG-archive.md) on 2026-05-15** —
chronicle of the build from extraction prototype through Faktomat's
first paying flow. Active log above closes Phase 6 and tracks the
post-launch night work (2026-05-14/15: re-extract on failed stub,
friendly Polish errors, public /status page, VM migration, billing
gate on re-extract, extraction alerter).
