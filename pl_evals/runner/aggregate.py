from __future__ import annotations
import json
import re
import statistics
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .scoring import field_accuracy, schema_validity


@dataclass
class ModelScore:
    model_id: str
    provider: str
    field_accuracy: float
    schema_validity: float
    latency_p50: int  # ms; 0 if no successful runs
    cost_per_1k: float  # estimated, in USD for 1k calls at observed token usage
    errors: int
    composite: float


_FENCE_RE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*\n(.*?)\n```\s*$", re.DOTALL)


def _strip_markdown_fence(text: str) -> str:
    """Strip a leading ```json / ```yaml / ``` and trailing ``` if the
    entire payload is wrapped in a single markdown code fence — standard
    production cleanup before json.loads. Conservative: only strips if the
    whole string matches; partial fences are left for parser to fail on."""
    m = _FENCE_RE.match(text)
    return m.group(1) if m else text


def _safe_parse_json(text: str) -> dict[str, Any] | None:
    try:
        return json.loads(_strip_markdown_fence(text))
    except (json.JSONDecodeError, TypeError):
        return None


def aggregate_run(
    *,
    run_path: Path,
    task_schema: dict[str, Any],
    cases: list[dict[str, Any]],
    models_meta: list[dict[str, Any]],
    scoring_weights: dict[str, float],
    task_name: str,
) -> dict[str, Any]:
    """Read a run JSONL + task config, compute per-model composite scores,
    return the site's snapshot dict."""
    case_by_id = {c["id"]: c for c in cases}

    # Group records by model
    records_by_model: dict[str, list[dict[str, Any]]] = {}
    with run_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            records_by_model.setdefault(rec["model_id"], []).append(rec)

    model_scores: list[dict[str, Any]] = []
    for meta in models_meta:
        model_id = meta["id"]
        recs = records_by_model.get(model_id, [])
        errors = sum(1 for r in recs if "error" in r)
        successes = [r for r in recs if "error" not in r]

        per_case_field_acc: list[float] = []
        schema_valid_count = 0
        latencies: list[int] = []
        input_tokens_total = 0
        output_tokens_total = 0

        for rec in successes:
            parsed = _safe_parse_json(rec.get("output_text", ""))
            if parsed is None:
                per_case_field_acc.append(0.0)
                continue
            gt = case_by_id[rec["case_id"]]["expected_output"]
            per_case_field_acc.append(field_accuracy(parsed, gt))
            if schema_validity(parsed, task_schema):
                schema_valid_count += 1
            latencies.append(rec.get("latency_ms", 0))
            input_tokens_total += rec.get("input_tokens", 0)
            output_tokens_total += rec.get("output_tokens", 0)

        n_attempted = len(recs)
        field_acc = (
            statistics.mean(per_case_field_acc) if per_case_field_acc else 0.0
        )
        schema_val = (schema_valid_count / n_attempted) if n_attempted else 0.0
        latency_p50 = int(statistics.median(latencies)) if latencies else 0

        n_success = len(successes)
        if n_success > 0:
            avg_in = input_tokens_total / n_success
            avg_out = output_tokens_total / n_success
            cost_per_call = (
                (avg_in / 1_000_000) * meta.get("cost_per_1m_in", 0.0)
                + (avg_out / 1_000_000) * meta.get("cost_per_1m_out", 0.0)
            )
            cost_per_1k = cost_per_call * 1000
        else:
            cost_per_1k = 0.0

        latency_norm = 0.0
        cost_norm = 0.0
        if scoring_weights.get("latency_p50", 0) > 0 and latencies:
            # Cap at 30s, linear.
            latency_norm = max(0.0, 1.0 - (latency_p50 / 30_000))
        if scoring_weights.get("cost_per_1k", 0) > 0 and cost_per_1k > 0:
            # Cap at $10/1k calls.
            cost_norm = max(0.0, 1.0 - (cost_per_1k / 10.0))

        composite = (
            scoring_weights["field_accuracy"] * field_acc
            + scoring_weights["schema_validity"] * schema_val
            + scoring_weights.get("latency_p50", 0) * latency_norm
            + scoring_weights.get("cost_per_1k", 0) * cost_norm
        )

        score = ModelScore(
            model_id=model_id,
            provider=meta["provider"],
            field_accuracy=round(field_acc, 4),
            schema_validity=round(schema_val, 4),
            latency_p50=latency_p50,
            cost_per_1k=round(cost_per_1k, 4),
            errors=errors,
            composite=round(composite, 4),
        )
        model_scores.append(asdict(score))

    model_scores.sort(key=lambda m: m["composite"], reverse=True)

    return {
        "task": task_name,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_cases": len(cases),
        "scoring_weights": scoring_weights,
        "models": model_scores,
    }
