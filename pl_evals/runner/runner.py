"""pl-evals CLI: load task.yaml, dispatch (case x model), write JSONL."""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import yaml

from .providers.base import Provider, ProviderResult, ProviderError
from .providers.openrouter import OpenRouterProvider
from .providers.groq import GroqProvider
from .providers.hf_inference import HFInferenceProvider


@dataclass
class TaskConfig:
    name: str
    description: str
    version: int
    schema: dict[str, Any]
    prompt_template: str
    cases: list[dict[str, Any]]
    scoring: dict[str, float]
    models: list[dict[str, Any]]
    base_dir: Path


def load_task(task_yaml_path: Path) -> TaskConfig:
    base = Path(task_yaml_path).parent
    raw = yaml.safe_load(Path(task_yaml_path).read_text())
    schema_path = base / raw["schema"]
    prompt_path = base / raw["prompt"]
    cases_path = base / raw["cases"]

    schema = json.loads(schema_path.read_text())
    prompt_template = prompt_path.read_text()
    cases = [json.loads(line) for line in cases_path.read_text().splitlines() if line.strip()]

    return TaskConfig(
        name=raw["name"],
        description=raw.get("description", ""),
        version=raw.get("version", 1),
        schema=schema,
        prompt_template=prompt_template,
        cases=cases,
        scoring=raw["scoring"],
        models=raw["models"],
        base_dir=base,
    )


def get_provider(provider_name: str) -> Provider:
    """Map provider name from task.yaml to an instantiated Provider.
    Reads API keys from env."""
    if provider_name == "openrouter":
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        return OpenRouterProvider(api_key=key)
    if provider_name == "groq":
        key = os.environ.get("GROQ_API_KEY", "")
        if not key:
            raise RuntimeError("GROQ_API_KEY not set")
        return GroqProvider(api_key=key)
    if provider_name == "hf_inference":
        key = os.environ.get("HF_TOKEN", "")
        if not key:
            raise RuntimeError("HF_TOKEN not set")
        return HFInferenceProvider(api_key=key)
    raise ValueError(f"unknown provider: {provider_name}")


def _render_prompt(template: str, case_input: str) -> str:
    return template.replace("{{invoice_text}}", case_input)


async def run_task(cfg: TaskConfig, out_path: Path) -> None:
    """Run every (case × model) combination, one record per line in JSONL."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for model in cfg.models:
            try:
                provider = get_provider(model["provider"])
            except RuntimeError as e:
                # Missing env-var API key for this provider. Skip the model:
                # write one error record per case so the aggregator surfaces
                # it as errors=N instead of silently dropping the model.
                for case in cfg.cases:
                    rec = {
                        "task": cfg.name,
                        "case_id": case["id"],
                        "model_id": model["id"],
                        "provider": model["provider"],
                        "error": {"message": str(e), "retryable": False},
                    }
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                continue
            for case in cfg.cases:
                prompt = _render_prompt(cfg.prompt_template, case["input"])
                rec: dict[str, Any] = {
                    "task": cfg.name,
                    "case_id": case["id"],
                    "model_id": model["id"],
                    "provider": model["provider"],
                }
                try:
                    result: ProviderResult = await provider.complete(prompt, model["id"])
                    rec.update({
                        "output_text": result.text,
                        "latency_ms": result.latency_ms,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                    })
                except ProviderError as e:
                    rec["error"] = {"message": str(e), "retryable": e.retryable}
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main() -> int:
    """Entry point for `pl-evals` console script.
    Subcommands: run <task.yaml> | aggregate <task.yaml> <run.jsonl>"""
    if len(sys.argv) < 2:
        print("usage: pl-evals {run <task.yaml> | aggregate <task.yaml> <run.jsonl>}", file=sys.stderr)
        return 2
    cmd = sys.argv[1]
    if cmd == "run":
        if len(sys.argv) < 3:
            print("usage: pl-evals run <task.yaml>", file=sys.stderr)
            return 2
        task_path = Path(sys.argv[2])
        cfg = load_task(task_path)
        ts = time.strftime("%Y%m%dT%H%M%S")
        out = Path("results") / f"{cfg.name}-{ts}.jsonl"
        asyncio.run(run_task(cfg, out))
        print(f"wrote {out}")
        return 0
    if cmd == "aggregate":
        from .aggregate import aggregate_run
        if len(sys.argv) < 4:
            print("usage: pl-evals aggregate <task.yaml> <run.jsonl>", file=sys.stderr)
            return 2
        task_path = Path(sys.argv[2])
        run_path = Path(sys.argv[3])
        cfg = load_task(task_path)
        snapshot = aggregate_run(
            run_path=run_path,
            task_schema=cfg.schema,
            cases=cfg.cases,
            models_meta=cfg.models,
            scoring_weights=cfg.scoring,
            task_name=cfg.name,
        )
        out = Path("site") / "data.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
        print(f"wrote {out}")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2
