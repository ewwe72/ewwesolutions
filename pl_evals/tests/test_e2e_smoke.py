"""End-to-end smoke test: load real task.yaml, mock providers, run full pipeline."""
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from runner.runner import load_task, run_task
from runner.aggregate import aggregate_run
from runner.providers.base import ProviderResult

REPO_ROOT = Path(__file__).resolve().parents[1]
INVOICE_TASK = REPO_ROOT / "tasks" / "invoice_extraction" / "task.yaml"


@pytest.mark.asyncio
async def test_e2e_run_then_aggregate(tmp_path):
    """Load real invoice_extraction task, mock all providers, run pipeline."""
    cfg = load_task(INVOICE_TASK)
    assert len(cfg.cases) >= 2  # seed.jsonl from Task 2 has 10 cases
    assert len(cfg.models) >= 1  # task.yaml from Task 2 has 5 models

    # Mock provider returns case-001's ground truth for every call
    fake_provider = AsyncMock()
    gt = cfg.cases[0]["expected_output"]
    fake_provider.complete.return_value = ProviderResult(
        text=json.dumps(gt, ensure_ascii=False),
        model_id=cfg.models[0]["id"],
        latency_ms=100,
        input_tokens=500,
        output_tokens=200,
        raw={},
    )

    run_path = tmp_path / "smoke.jsonl"
    with patch("runner.runner.get_provider", return_value=fake_provider):
        await run_task(cfg, run_path)

    # Verify run JSONL was written
    n_records = sum(1 for line in run_path.read_text().splitlines() if line.strip())
    assert n_records == len(cfg.cases) * len(cfg.models), \
        f"Expected {len(cfg.cases) * len(cfg.models)} records, got {n_records}"

    # Aggregate and validate snapshot shape
    snapshot = aggregate_run(
        run_path=run_path,
        task_schema=cfg.schema,
        cases=cfg.cases,
        models_meta=cfg.models,
        scoring_weights=cfg.scoring,
        task_name=cfg.name,
    )

    assert snapshot["task"] == "invoice_extraction"
    assert snapshot["n_cases"] == len(cfg.cases)
    assert len(snapshot["models"]) == len(cfg.models)

    # All models returned the same fake output (case-001's GT) for every case.
    # Only case-001 matches; other 9 cases will score 0 → average field_acc ≈ 1/10 = 0.1
    top = snapshot["models"][0]
    assert 0.05 <= top["field_accuracy"] <= 0.2, \
        f"Expected field_accuracy ~0.1 (1 match out of 10), got {top['field_accuracy']}"
    assert top["schema_validity"] == 1.0, \
        f"Expected schema_validity = 1.0 (GT is always valid), got {top['schema_validity']}"
