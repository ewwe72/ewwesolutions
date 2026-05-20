# pl_evals — operator handoff (v0)

## State

v0 shipped. Pipeline works end-to-end against mocked providers
(25 tests pass). Real-API run is gated behind operator providing
keys via `.env`.

## Pre-launch checklist

- [ ] Provide all three API keys in `pl_evals/.env` (template:
      `.env.example`)
- [ ] Run real eval:
      `cd pl_evals && pl-evals run tasks/invoice_extraction/task.yaml`
- [ ] Aggregate:
      `pl-evals aggregate tasks/invoice_extraction/task.yaml results/<latest>.jsonl`
- [ ] Visually inspect `site/data.json` — sanity-check the leaderboard
- [ ] Serve `site/` via `python3 -m http.server` and visually inspect
      the rendered page

## What's in v0

5 models (Claude Sonnet 4.6, GPT-5, Gemini 2.5 Pro via OpenRouter;
Llama 3.3 70B via Groq; Bielik 11B v2.3 via HF Inference) × 10
synthetic Polish invoice cases. Per-run cost target: $2-5.

Scoring: composite = 0.7 field_accuracy + 0.2 schema_validity + 0.05
latency_p50 + 0.05 cost_per_1k.

## Out of scope (deferred)

**v0.1** — replace synthetic seed.jsonl with operator-verified real
invoices pulled from operator's Gmail:
1. Read existing tooling first: `invoice_idp/scripts/curate_eval_set.py`,
   `dedup_eval_set.py`, `run_eval.py`, `spotcheck.py`, `gmail_pull.py`.
2. Pull batch via `gmail_pull.py` (creds in `invoice_idp/.env`:
   `GMAIL_EMAIL` + `GMAIL`) to a private staging dir outside the repo.
3. **Mandatory classification step** — raw gmail_pull is "pies z budą"
   (lots of non-invoice PDFs). Haiku classifier per PDF page-1 →
   {invoice|not_invoice|maybe|empty}. Maybe queue, not auto-discard.
4. pymupdf for text extraction.
5. faktomat Bedrock Opus → candidate ground truth JSONL.
6. CLI verify-loop: operator goes case-by-case, accepts/corrects fields.
7. Replace `cases/seed.jsonl` reference in task.yaml with `cases/real.jsonl`.

**v0.2** — paid "request fresh run" button on the leaderboard site.
Stripe Checkout, ~$20/rerun. Operator-funded launch run only; paid
reruns thereafter. Job dedup if a run is in progress.

**v0.5+**:
- LLM-as-judge for Polish-language quality (Claude Opus 4.7 as judge,
  prompt versioned in `judges/`, 20% human-validation sample)
- Weekly cron rerun via systemd timer
- Public subdomain `evals.ewwesolutions.work` via existing
  cloudflared tunnel
- Repo migration to public `ewwe72/ewwesolutions/pl_evals/` via
  `git subtree split`
- Add Opus 4.7 (reasoning-tier baseline), DeepSeek (open reasoning),
  PLLuM (after self-hosting on Together AI free credits to avoid
  HF cold-start latency noise)

## Known limitations

- Bielik / PLLuM cold-start latency on HF Inference free tier
  produces unreliable latency numbers. Documented as a caveat on
  the methodology page.
- v0 cost-per-1k uses list pricing from `task.yaml` × observed
  token usage. No discount/spend-tier awareness.
- Synthetic-only seed data — replace before launch (see v0.1).

## Repo migration plan (deferred, separate plan)

When code is reviewed and ready public:
1. Create `pl_evals/` subdir in `ewwe72/ewwesolutions` (public repo).
2. `git subtree split --prefix=pl_evals -b pl_evals-export` to
   extract commit history.
3. Push that branch into the public repo.
4. Update README links from `github.com/ewwe72/random` to
   `github.com/ewwe72/ewwesolutions`.
5. Add CI (GitHub Actions): pytest on PR.
