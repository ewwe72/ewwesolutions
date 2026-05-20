import pytest
from pathlib import Path
from runner.aggregate import aggregate_run

FIXTURES = Path(__file__).parent / "fixtures"


def test_aggregate_run_scores_two_models_one_errors():
    task_schema = {"type": "object", "required": ["k"], "properties": {"k": {"type": "integer"}}}
    cases = [{"id": "c1", "input": "x", "expected_output": {"k": 1}}]
    models_meta = [
        {"id": "test/model-1", "provider": "openrouter", "cost_per_1m_in": 1.0, "cost_per_1m_out": 2.0},
        {"id": "test/model-2", "provider": "openrouter", "cost_per_1m_in": 5.0, "cost_per_1m_out": 10.0},
        {"id": "test/model-3", "provider": "openrouter", "cost_per_1m_in": 0.0, "cost_per_1m_out": 0.0},
    ]
    scoring_weights = {"field_accuracy": 0.8, "schema_validity": 0.2, "latency_p50": 0.0, "cost_per_1k": 0.0}

    snapshot = aggregate_run(
        run_path=FIXTURES / "sample_results" / "run1.jsonl",
        task_schema=task_schema,
        cases=cases,
        models_meta=models_meta,
        scoring_weights=scoring_weights,
        task_name="sample",
    )

    by_id = {m["model_id"]: m for m in snapshot["models"]}
    assert by_id["test/model-1"]["field_accuracy"] == 1.0
    assert by_id["test/model-1"]["schema_validity"] == 1.0
    assert by_id["test/model-1"]["composite"] == 1.0

    assert by_id["test/model-2"]["field_accuracy"] == 0.0  # k=99 != 1
    assert by_id["test/model-2"]["schema_validity"] == 1.0  # still valid JSON+schema
    assert by_id["test/model-2"]["composite"] == pytest.approx(0.2)

    assert by_id["test/model-3"]["errors"] == 1
    assert by_id["test/model-3"]["composite"] == 0.0  # all attempts errored

    assert snapshot["task"] == "sample"
    assert "generated_at" in snapshot
    assert snapshot["n_cases"] == 1
    # Models sorted by composite desc → model-1 first
    assert snapshot["models"][0]["model_id"] == "test/model-1"
