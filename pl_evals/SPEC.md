# pl_evals — Polish AI Production Eval Leaderboard

**Status**: design spec, no code yet
**Date**: 2026-05-20
**Owner**: ewwe72
**Codename**: `pl_evals` (public-facing name TBD)

## TL;DR

First public leaderboard scoring closed + open LLMs on **vertical production
tasks** relevant to Polish SaaS devs, with **Polish-language quality** scored
as a first-class dimension on every task. Where existing PL benchmarks (KLEJ,
PolEval, PL-MMLU) measure academic NLP accuracy, this answers the question a
working dev actually asks: *"which model do I pick if I'm shipping a
Polish-language feature in prod next week?"*

Operator's edge: builds vertical tasks on top of products the operator has
already shipped in production (faktomat, fiszkomat), so the tasks are not
synthetic — they mirror real workloads.

## Why this exists (positioning vs. prior art)

| Existing | What it measures | Gap |
|---|---|---|
| KLEJ (Allegro, 2020) | Academic NLP (sentiment, NER, NLI) | Old, contaminated, not production-shaped |
| PolEval (annual) | Shared-task academic series | Researcher audience, not dev audience |
| PL-MMLU | General-knowledge MCQ | No bearing on real PL deployment choices |
| Bielik internal evals | SpeakLeash-team-private | Not public, not comparative across providers |

**This project's wedge**: production-shaped tasks + Polish quality as a
scored axis + closed and open models side-by-side + weekly re-run as new
models drop.

**Target persona**: Polish SaaS developer evaluating which LLM to embed in
their product. Secondary: PL AI community curious about Bielik/PLLuM vs.
frontier model quality.

## v0 scope (3 days)

**One task only.** Ship narrow and credible.

### Task 1: Polish invoice extraction

- **Input**: a PL invoice (text rendering of a PDF, ~1-3k tokens)
- **Output**: structured JSON conforming to a defined schema (NIP, dates,
  line items, totals, VAT rates)
- **Why this task**:
  - Operator has prompt-engineered this in production (faktomat) — domain
    expertise transferable
  - Objective scoring on extraction fields (no LLM-as-judge needed for the
    core metric)
  - Public dataset assemblable from gov.pl JPK examples + synthetic
    augmentation; no customer data
  - The Polish-quality angle plugs in via the free-text fields the model
    generates (descriptions, notes)

### Models for v0 run

Mix of providers, mostly cheap or free at eval scale:

**Closed (via OpenRouter, one API key)**:
- Claude Sonnet 4.6
- Claude Opus 4.7
- GPT-5 (or latest available)
- Gemini 2.5 Pro

**Open via Groq (free tier) or HF Inference (free)**:
- Llama 3.3 70B
- DeepSeek V3

**Polish-specific (HF Inference or Together free credits)**:
- Bielik 11B v2.3 Instruct
- PLLuM (latest checkpoint)

Total: ~8 models in v0. Cost ceiling per full run ≈ $5. Stay under $25/mo
with weekly reruns and v0.5 additions.

## Scoring

Per task, multiple dimensions, weighted into a composite leaderboard score
(weights configurable via UI sliders).

**Live in v0:**
1. **Field accuracy** (objective) — field-level F1 against ground truth
2. **Schema validity** (objective) — % of outputs that parse and conform
3. **Latency** — p50, p95 from provider
4. **Cost per 1k calls** — at current provider list pricing

**Added in v0.5:**
5. **Polish quality** of free-text generated fields (LLM-as-judge,
   validated against 20% human-graded subset) — rubric covers declination,
   register, idiom-vs-calque, B1/B2/C1/native banding

**LLM-as-judge bias note** (v0.5+): judge model is fixed as Claude Opus 4.7
(a reasoning-tier model, not used as an extraction-tier subject), with
prompt versioned in `judges/`. The 20% human-validation sample is the
calibration anchor — operator (native PL speaker) grades it manually — if
judge-vs-human Cohen's κ drops below 0.6 on any re-run, results are
flagged "low-confidence" on the site.

## Architecture

Small, isolated units. Each one independently runnable.

```
pl_evals/
├── runner/              # Python eval orchestrator
│   ├── providers/       # OpenRouter, Groq, HF, Bedrock adapters
│   ├── runner.py        # main entry; reads task YAML, dispatches, scores
│   └── scoring.py       # objective metrics (F1, schema validity)
├── tasks/
│   └── invoice_extraction/
│       ├── task.yaml    # schema, prompts, model list
│       ├── cases/       # JSONL of test cases with ground truth
│       └── README.md    # methodology, data sources
├── judges/
│   └── polish_quality.md   # versioned LLM-as-judge prompt
├── results/             # JSONL outputs, one file per run, timestamped
├── site/
│   ├── index.html       # static leaderboard
│   ├── data.json        # latest results snapshot
│   └── methodology.html # how scoring works, judge prompt, validation
├── cron/
│   └── weekly_rerun.sh  # systemd timer entrypoint
└── SPEC.md              # this file
```

Boundaries:
- `runner/` knows nothing about HTML — just produces `results/*.jsonl`
- `site/` reads `data.json` only — no live API calls, fully static
- `tasks/` are data + config, no Python — anyone can submit a new task as a
  PR without touching runner code
- Providers are pluggable: adding a new model = one entry in `task.yaml` if
  it's an OpenRouter model, or a new provider adapter file otherwise

## Infra & hosting

Runs from existing claude-sandbox VM. No new hosting cost.

- **Eval runner**: Python script invoked manually for v0, systemd timer
  weekly for v0.5+
- **Leaderboard site**: static files served by new systemd unit
  `pl-evals-site.service` (mirrors pattern of `ewwesolutions-studio.service`)
- **Subdomain**: `evals.ewwesolutions.work` via existing cloudflared tunnel
  (add a new ingress rule)
- **Storage**: results committed to public repo (each run = one JSONL
  appended), so history is browsable in git

## Public artifacts

- **Repo**: lives in `ewwe72/ewwesolutions` public monorepo under
  `pl_evals/` subdir (NOT the internal `random` repo)
- **Site**: `evals.ewwesolutions.work` with leaderboard, methodology page,
  raw results downloadable
- **Launch post**: short writeup (PL + EN) explaining what this measures
  and what it doesn't, posted to operator's Twitter/LinkedIn for AI Dev
  visibility
- **README** points at site, lists how to submit a task or model

## Milestones

| Milestone | Scope | Time |
|---|---|---|
| **v0** | Task 1 only. ~8 models. Static leaderboard with objective scoring (no Polish-quality judge yet — that's v0.5). Public repo. | 3 days |
| **v0.5** | Add Polish-quality LLM-as-judge with rubric and human-validation calibration. Weekly cron rerun. Twitter/LI post. | +2 days |
| **v1** | Add Task 2 (candidate: formal-document generation, e.g. "pismo do ZUS"). Submission form for community-contributed tasks. Blog post on methodology. | +1 week |

## Out of scope for v0

- Multi-task aggregate ranking (only one task)
- Human eval program beyond the 20% judge-calibration sample
- Fine-tuning or training anything
- Contamination detection on training data
- API for third-party submissions (manual PRs only)
- Twilio / phone / anything paid beyond model API costs

## Open questions

1. **Dataset sourcing for invoice ground truth** — how many of the 100
   test cases come from public gov.pl JPK examples vs. operator-generated
   synthetic vs. operator's own redacted invoices? Affects how shareable
   the dataset is and how representative it is of real-world variance.
2. **Public-facing name** — `pl_evals` is internal codename only.
   Candidates: `PolEval-Pro`, `LLMpopolsku`, `MowiszPoPolsku`,
   `PolskiBench`. Needs to be searchable, not collide with PolEval shared
   task series, not embarrassing on a CV.
3. **Bielik / PLLuM endpoint reliability** — HF Inference has rate limits
   and cold-start latency that may invalidate latency comparisons. Decide
   v0 whether to (a) accept that and document it, or (b) host Bielik
   ourselves on Together AI free credits for consistency.
4. **Public-deploy authorization** — putting a new subdomain live and
   pointing it at a leaderboard with named third-party models needs
   operator green-light. Per memory: live-deploy moves are gated.

## Risks

- **Methodology pushback** — academic PL NLP community may dismiss this
  as underdesigned. Mitigation: own that framing in the methodology page
  ("this is a production-quality benchmark, not a research one"), cite
  prior work, link to KLEJ/PolEval as complementary.
- **Judge-bias accusations** — using Claude Opus as judge of an
  extraction task whose subjects include Claude variants invites the
  "they picked Claude as judge to favor Claude" critique. Mitigation:
  publish the 20% human-validation κ scores prominently, publish judge
  prompt, allow community to rerun with different judge via the runner.
- **Bielik / PLLuM team backlash** — if the leaderboard shows Polish
  open models trailing frontier closed models (very likely), the SpeakLeash
  community may push back. Mitigation: frame their numbers in context
  (cost-per-call, on-prem feasibility, fine-tuning headroom) — these are
  legitimate axes where open-PL models win.
- **Time blow-out** — operator estimates portfolio-velocity is days, not
  weeks, but this project has more moving parts than fiszkomat or
  faktomat's first phase. Mitigation: enforce v0 scope strictly. If v0
  isn't shippable in 3 days, cut models (drop to 5) before cutting the
  Polish-quality scoring dimension.
