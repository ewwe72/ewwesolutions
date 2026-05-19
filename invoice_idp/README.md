# Faktomat — Polish invoice OCR → JPK_FA(4)

Drop a Polish invoice PDF (faktura VAT), get back a
[JPK_FA(4)](https://www.podatki.gov.pl/jednolity-plik-kontrolny/) XML
file validated against the Ministry of Finance schema, plus JSON and
CSV exports. Pay-as-you-go at 0.50 PLN/invoice, no subscription. Data
stays in the EU (AWS Frankfurt).

**Live:** [faktomat.ewwesolutions.work](https://faktomat.ewwesolutions.work)
**Status:** 🟢 Phase 6 LIVE — first end-to-end paying flow worked
2026-05-14 (signup → email-verify → Stripe Checkout → upload PDF →
Bedrock Haiku extraction → editable review → JPK_FA export).

## Stack

Python 3.11 · FastAPI · async SQLAlchemy + Postgres · Anthropic SDK
(AWS Bedrock, `eu.anthropic.claude-haiku-4-5`) · arq worker · Stripe ·
Postmark · MinIO/S3 · lxml (JPK_FA XML build) · Jinja2 templates ·
Tailwind via CDN. Tested with pytest + mypy `--strict`.

## How it runs

Production runs as `docker-compose.vm.yml` on a Hyper-V Ubuntu 24.04
VM. cloudflared is a systemd unit inside the VM and fronts the request
path on loopback. Full topology, day-2 ops, and rollback procedure in
[`docs/vm-migration-2026-05-14.md`](../docs/vm-migration-2026-05-14.md).

### Production update (on the VM)

```bash
cd ~/playspace/random/invoice_idp
git pull origin main
docker compose -f docker-compose.vm.yml exec app alembic upgrade head
docker compose -f docker-compose.vm.yml up -d --build
docker compose -f docker-compose.vm.yml logs -f app worker
```

No `--reload` in the VM compose — **every** code change (templates,
routes, workers, models, prompts, deps, Dockerfile) requires the
rebuild.

### Local dev

```bash
cd invoice_idp
docker compose up -d                # postgres + redis + minio + minio-init
alembic upgrade head
uvicorn src.app.main:app --reload --port 8000   # terminal 1
python -m arq src.app.workers.extract.WorkerSettings   # terminal 2
```

Browser endpoints:
- `http://localhost:8000` — landing
- `http://localhost:8000/signup` / `/login` — auth
- `http://localhost:8000/app` — dashboard (after login)
- `http://localhost:8000/docs` — OpenAPI / Swagger
- `http://localhost:9001` — MinIO console (`minioadmin` / `minioadmin`)

Env vars: copy `.env.example` to `.env` and fill in the keys you have
(at minimum: `ANTHROPIC_API_KEY` or AWS Bedrock creds + `BIGGA`
Discord webhook). Full key list and which phase needs which: see
[`SPEC.md`](SPEC.md) §0 + [`HANDOFF.md`](HANDOFF.md) §Environment.

## Tests

```bash
pytest tests/                       # 130 test functions
mypy --strict src/                  # 0 errors expected
```

Integration tests need `docker compose up -d` first (Postgres + Redis
+ MinIO). Unit tests don't.

## Deeper reading

- [`SPEC.md`](SPEC.md) — full product spec (v1.3): scope, data model,
  extraction pipeline, output formats, REST API, web UI, auth/billing,
  hosting, pricing, build phases, risks.
- [`HANDOFF.md`](HANDOFF.md) — session-handoff notes: gotchas
  ("don't re-hit these"), env vars on prod, runbook, Twilio status,
  Phase 6 launch gate state.
- [`CHANGELOG.md`](CHANGELOG.md) — chronological build log, every
  chunk + rationale. Latest entries at top.
- [`docs/vm-migration-2026-05-14.md`](../docs/vm-migration-2026-05-14.md)
  — the VM migration runbook (topology, day-2, rollback).
