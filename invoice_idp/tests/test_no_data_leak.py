"""Invariant tests for the extraction layer — the `test_lookahead.py` analog.

Per spec §0: "no test data may leak into prompts, no future-dated
information can appear in extracted output". These are cheap guards
against subtle bugs the rest of the test suite would miss.

The list of forbidden real-customer strings lives in the `.env` under
`TEST_FORBIDDEN_STRINGS` (comma-separated). Keeping these in env keeps
the canaries out of source — the canary file used to leak the very
data it was guarding. When the env var is unset (CI without secrets,
fresh clone), the data-leak tests self-skip so the rest of the suite
still runs green.
"""

from __future__ import annotations

import os
import re

import pytest

from src.pipeline.extraction.anthropic_provider import PROMPT_PATH, TOOL_SCHEMA
from src.pipeline.extraction.bedrock_provider import BEDROCK_MODEL_MAP


def _forbidden_strings() -> list[str]:
    raw = os.environ.get("TEST_FORBIDDEN_STRINGS", "")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def test_prompt_contains_no_real_customer_data() -> None:
    needles = _forbidden_strings()
    if not needles:
        pytest.skip("TEST_FORBIDDEN_STRINGS not set in env — canary list empty")
    text = _prompt()
    for needle in needles:
        assert needle not in text, f"Real-data leak in prompt: {needle!r}"


def test_prompt_contains_no_secrets() -> None:
    """Anthropic / AWS / Postmark tokens have recognisable prefixes."""
    text = _prompt()
    forbidden_patterns = [
        r"sk-ant-",                # Anthropic key prefix
        r"\bAKIA[A-Z0-9]{16}\b",   # AWS access key
        r"sk_live_",               # Stripe live key
        r"sk_test_",               # Stripe test key
    ]
    for pattern in forbidden_patterns:
        assert not re.search(pattern, text), f"Secret-shaped token in prompt: {pattern}"


def test_tool_schema_enum_invariants() -> None:
    """The VAT-rate enum is the single source of truth — schema must match models."""
    line_def = TOOL_SCHEMA["$defs"]["line"]
    rate_enum = line_def["properties"]["vat_rate"]["enum"]
    assert set(rate_enum) == {"23", "8", "5", "0", "zw", "np", "oo"}, (
        f"VAT-rate enum drift in TOOL_SCHEMA: {rate_enum}"
    )

    currency_enum = TOOL_SCHEMA["$defs"]["money"]["properties"]["currency"]["enum"]
    assert set(currency_enum) == {"PLN", "EUR", "USD", "GBP", "CHF", "CZK"}, (
        f"Currency enum drift in TOOL_SCHEMA: {currency_enum}"
    )


def test_bedrock_model_map_uses_eu_inference_profile() -> None:
    """Per §17.8: Bedrock must stay in EU. Profile prefix `eu.` enforces this."""
    for canonical, bedrock_id in BEDROCK_MODEL_MAP.items():
        assert bedrock_id.startswith("eu."), (
            f"Bedrock model {canonical!r} maps to {bedrock_id!r} — not an EU inference profile"
        )


def test_tool_schema_has_no_test_data() -> None:
    """Descriptions / examples in the tool schema must be generic."""
    import json
    needles = _forbidden_strings()
    if not needles:
        pytest.skip("TEST_FORBIDDEN_STRINGS not set in env — canary list empty")
    serialised = json.dumps(TOOL_SCHEMA, ensure_ascii=False)
    for needle in needles:
        assert needle not in serialised, f"Real-data leak in TOOL_SCHEMA: {needle!r}"
