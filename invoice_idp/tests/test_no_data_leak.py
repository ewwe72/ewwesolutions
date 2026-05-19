"""Invariant tests for the extraction layer — the `test_lookahead.py` analog.

Per spec §0: "no test data may leak into prompts, no future-dated
information can appear in extracted output". These are cheap guards
against subtle bugs the rest of the test suite would miss.
"""

from __future__ import annotations

import re

from src.pipeline.extraction.anthropic_provider import PROMPT_PATH, TOOL_SCHEMA
from src.pipeline.extraction.bedrock_provider import BEDROCK_MODEL_MAP

# Real-customer data from the eval set that must NEVER appear in shipped prompts.
# Add new entries when new high-value seeds are introduced.
LEAKED_DATA_FORBIDDEN_IN_PROMPT = [
    "5981250614",         # ebratek seller NIP
    "266/5/2026/BL",      # ebratek invoice number
    "Jolanta Bratkowska",
    "Patryk Popenda",
    "thorgal7295",        # operator's gmail handle
    "stefanini.com",      # operator's work-email domain
]


def _prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def test_prompt_contains_no_real_customer_data() -> None:
    text = _prompt()
    for needle in LEAKED_DATA_FORBIDDEN_IN_PROMPT:
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
    serialised = json.dumps(TOOL_SCHEMA, ensure_ascii=False)
    for needle in LEAKED_DATA_FORBIDDEN_IN_PROMPT:
        assert needle not in serialised, f"Real-data leak in TOOL_SCHEMA: {needle!r}"
