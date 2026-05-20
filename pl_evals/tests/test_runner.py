import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from runner.runner import load_task, run_task, TaskConfig
from runner.providers.base import ProviderResult

FIXTURES = Path(__file__).parent / "fixtures"


def test_load_task_parses_yaml():
    cfg = load_task(FIXTURES / "sample_task.yaml")
    assert isinstance(cfg, TaskConfig)
    assert cfg.name == "sample"
    assert len(cfg.models) == 1
    assert cfg.models[0]["id"] == "test/model-1"
    assert cfg.schema["required"] == ["k"]
    assert cfg.prompt_template == "prompt body {{invoice_text}}\n" or cfg.prompt_template == "prompt body {{invoice_text}}"
    assert len(cfg.cases) == 1
    assert cfg.cases[0]["expected_output"] == {"k": 1}


@pytest.mark.asyncio
async def test_run_task_dispatches_per_model_and_writes_jsonl(tmp_path):
    cfg = load_task(FIXTURES / "sample_task.yaml")
    fake_provider = AsyncMock()
    fake_provider.complete.return_value = ProviderResult(
        text='{"k": 1}',
        model_id="test/model-1",
        latency_ms=50,
        input_tokens=10,
        output_tokens=5,
        raw={},
    )

    out_path = tmp_path / "out.jsonl"
    with patch("runner.runner.get_provider", return_value=fake_provider):
        await run_task(cfg, out_path)

    lines = out_path.read_text().splitlines()
    assert len(lines) == 1  # 1 case × 1 model
    rec = json.loads(lines[0])
    assert rec["case_id"] == "c1"
    assert rec["model_id"] == "test/model-1"
    assert rec["output_text"] == '{"k": 1}'
    assert rec["latency_ms"] == 50
    assert rec["input_tokens"] == 10
    assert rec["output_tokens"] == 5
    assert "error" not in rec


@pytest.mark.asyncio
async def test_run_task_skips_model_when_provider_key_missing(tmp_path):
    """If get_provider raises RuntimeError (missing env-var key), the model
    is skipped: one error record per case, run continues to next model."""
    cfg = load_task(FIXTURES / "sample_task.yaml")
    out_path = tmp_path / "out.jsonl"
    with patch("runner.runner.get_provider", side_effect=RuntimeError("OPENROUTER_API_KEY not set")):
        await run_task(cfg, out_path)

    lines = out_path.read_text().splitlines()
    assert len(lines) == 1  # 1 case × 1 model, all errored
    rec = json.loads(lines[0])
    assert rec["model_id"] == "test/model-1"
    assert "error" in rec
    assert "OPENROUTER_API_KEY not set" in rec["error"]["message"]
    assert rec["error"]["retryable"] is False
    assert "output_text" not in rec
