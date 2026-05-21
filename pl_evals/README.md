# pl_evals

Polish AI Production Eval Leaderboard. See `SPEC.md` for design.

## Quick start (v0)

```
cd pl_evals
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Run an eval

```
pl-evals run tasks/invoice_extraction/task.yaml
```

Outputs land in `results/<task>-<timestamp>.jsonl`. Aggregate to site:

```
pl-evals aggregate tasks/invoice_extraction/task.yaml results/<run>.jsonl
```

Produces `site/data.json`. Serve `site/` statically to view the leaderboard.

## Repo & site

- Public source: [github.com/ewwe72/ewwesolutions/tree/main/pl_evals](https://github.com/ewwe72/ewwesolutions/tree/main/pl_evals)
- Live leaderboard: [leaderboard.ewwesolutions.work](https://leaderboard.ewwesolutions.work)
- License: MIT

## Adding a model or task

- New model on an existing provider: one line in `task.yaml` under `models:`.
- New provider: drop a file in `runner/providers/` implementing the `Provider` ABC.
- New task: create `tasks/<task_name>/` with `task.yaml`, `schema.json`,
  `prompt.md`, `cases/seed.jsonl`, and `README.md`.

See `SPEC.md` for scoring methodology and `tasks/invoice_extraction/README.md`
for the data sourcing example.
