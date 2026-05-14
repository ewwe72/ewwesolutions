"""Abstract LLM provider interface — Anthropic now, Bedrock from Phase 3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ExtractionResult:
    """Raw structured output from a single LLM call, before validation."""
    data: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int


class InvoiceExtractor(Protocol):
    """Implementations call a vision LLM and return structured invoice data."""

    def extract(self, images: list[bytes], model: str) -> ExtractionResult: ...
