"""Anthropic API provider — vision + tool-use for structured invoice extraction.

Phase 1 uses the direct Anthropic API (decision §17.8). Phase 3 switches
to AWS Bedrock via `bedrock_provider.py`; both reuse `_call_claude` here
since `anthropic.Anthropic` and `anthropic.AnthropicBedrock` expose an
identical `messages.create` surface.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Union, cast

import anthropic

from src.pipeline.extraction.provider import ExtractionResult

AnthropicClient = Union[anthropic.Anthropic, anthropic.AnthropicBedrock]

PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "extraction_v1.md"
TOOL_NAME = "extract_invoice"

# JSON Schema for the extract_invoice tool. Hand-written rather than
# generated from Pydantic so we control exactly what Claude sees; metadata
# fields (overall_confidence, extracted_at, ...) are populated by our code
# after the call and intentionally omitted here.
TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "invoice_number": {"type": "string"},
        "invoice_type": {
            "type": "string",
            "enum": ["VAT", "PROFORMA", "KOREKTA", "DUPLIKAT", "UPROSZCZONA", "PARAGON"],
        },
        "issue_date": {
            "type": "string",
            "description": "Data wystawienia, YYYY-MM-DD",
        },
        "sale_date": {
            "type": ["string", "null"],
            "description": "Data sprzedaży / wykonania usługi, YYYY-MM-DD; null if absent",
        },
        "place_of_issue": {"type": ["string", "null"]},
        "seller": {"$ref": "#/$defs/counterparty"},
        "buyer": {"$ref": "#/$defs/counterparty"},
        "lines": {"type": "array", "items": {"$ref": "#/$defs/line"}},
        "vat_summary": {"type": "array", "items": {"$ref": "#/$defs/vat_summary_entry"}},
        "total_net": {"$ref": "#/$defs/money"},
        "total_vat": {"$ref": "#/$defs/money"},
        "total_gross": {"$ref": "#/$defs/money"},
        "payment": {"$ref": "#/$defs/payment"},
        "notes": {"type": ["string", "null"]},
    },
    "required": [
        "invoice_number",
        "issue_date",
        "seller",
        "buyer",
        "lines",
        "vat_summary",
        "total_net",
        "total_vat",
        "total_gross",
    ],
    "$defs": {
        "money": {
            "type": "object",
            "properties": {
                "amount": {"type": "number", "description": "Decimal, 2 dp"},
                "currency": {
                    "type": "string",
                    "enum": ["PLN", "EUR", "USD", "GBP", "CHF", "CZK"],
                },
            },
            "required": ["amount", "currency"],
        },
        "counterparty": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "nip": {"type": ["string", "null"], "description": "10-digit Polish NIP, no separators"},
                "regon": {"type": ["string", "null"]},
                "address_line1": {"type": ["string", "null"]},
                "address_line2": {"type": ["string", "null"]},
                "postal_code": {"type": ["string", "null"]},
                "city": {"type": ["string", "null"]},
                "country": {"type": "string", "description": "ISO 3166-1 alpha-2, default PL"},
                "bank_account": {"type": ["string", "null"], "description": "IBAN"},
                "confidence": {
                    "type": "object",
                    "additionalProperties": {"type": "number"},
                    "description": "Per-field self-confidence 0..1, e.g. {\"name\": 0.95, \"nip\": 0.9}",
                },
            },
            "required": ["name"],
        },
        "line": {
            "type": "object",
            "properties": {
                "line_no": {"type": "integer"},
                "description": {"type": "string"},
                "quantity": {"type": "number"},
                "unit": {"type": "string"},
                "unit_price_net": {"$ref": "#/$defs/money"},
                "vat_rate": {
                    "type": "string",
                    "enum": ["23", "8", "5", "0", "zw", "np", "oo"],
                },
                "discount_pct": {"type": "number"},
                "net_value": {"$ref": "#/$defs/money"},
                "vat_value": {"$ref": "#/$defs/money"},
                "gross_value": {"$ref": "#/$defs/money"},
                "confidence": {
                    "type": "object",
                    "additionalProperties": {"type": "number"},
                },
            },
            "required": [
                "line_no",
                "description",
                "quantity",
                "unit_price_net",
                "vat_rate",
                "net_value",
                "vat_value",
                "gross_value",
            ],
        },
        "vat_summary_entry": {
            "type": "object",
            "properties": {
                "rate": {
                    "type": "string",
                    "enum": ["23", "8", "5", "0", "zw", "np", "oo"],
                },
                "net_total": {"$ref": "#/$defs/money"},
                "vat_total": {"$ref": "#/$defs/money"},
                "gross_total": {"$ref": "#/$defs/money"},
            },
            "required": ["rate", "net_total", "vat_total", "gross_total"],
        },
        "payment": {
            "type": "object",
            "properties": {
                "method": {"type": ["string", "null"]},
                "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                "paid": {"type": "boolean"},
                "paid_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                "bank_account": {"type": ["string", "null"]},
            },
        },
    },
}


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _call_claude(
    client: AnthropicClient,
    prompt: str,
    images: list[bytes],
    model: str,
    reported_model: str | None = None,
) -> ExtractionResult:
    """Shared invocation logic for both direct API and Bedrock clients.

    `reported_model` is what's recorded on ExtractionResult — useful when
    `model` is a Bedrock fully-qualified ID and we want telemetry to show
    the canonical short name (`claude-sonnet-4-6`) instead.
    """
    image_blocks: list[dict[str, Any]] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": base64.standard_b64encode(img).decode("ascii"),
            },
        }
        for img in images
    ]
    content_blocks: list[dict[str, Any]] = [
        *image_blocks,
        {"type": "text", "text": prompt},
    ]

    response = client.messages.create(
        model=model,
        max_tokens=4096,
        tools=[
            {
                "name": TOOL_NAME,
                "description": "Emit the structured invoice data extracted from the image(s).",
                "input_schema": TOOL_SCHEMA,
            }
        ],
        tool_choice={"type": "tool", "name": TOOL_NAME},
        messages=[{"role": "user", "content": content_blocks}],
    )

    tool_input: dict[str, Any] | None = None
    for block in response.content:
        if block.type == "tool_use" and block.name == TOOL_NAME:
            tool_input = cast(dict[str, Any], block.input)
            break
    if tool_input is None:
        raise RuntimeError(
            f"Model {model} did not call {TOOL_NAME}; "
            f"stop_reason={response.stop_reason}"
        )

    return ExtractionResult(
        data=tool_input,
        model=reported_model or model,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


class AnthropicExtractor:
    """Direct Anthropic API implementation of InvoiceExtractor."""

    def __init__(self, api_key: str) -> None:
        # max_retries bumped from SDK default (2) to absorb transient 529
        # OverloadedError spikes during peak API load.
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=5)
        self._prompt = _load_prompt()

    def extract(self, images: list[bytes], model: str) -> ExtractionResult:
        return _call_claude(self._client, self._prompt, images, model)
