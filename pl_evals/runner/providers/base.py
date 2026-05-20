from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProviderResult:
    """Result of one model completion. The shape every provider must return."""
    text: str
    model_id: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    raw: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """Raised when a provider call fails. `retryable=True` means transient
    (rate limit, 5xx) — runner may retry. `retryable=False` is fatal."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class Provider(ABC):
    """One per upstream API (OpenRouter, Groq, HF Inference, ...).
    Knows nothing about tasks or scoring — just turns (prompt, model_id) into text."""

    @abstractmethod
    async def complete(self, prompt: str, model_id: str) -> ProviderResult:
        ...
