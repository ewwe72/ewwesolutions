"""AWS Bedrock provider — Claude via Bedrock for EU data residency.

Per SPEC §17 decision 8: Phase 3 switches the worker from the direct
Anthropic API to AWS Bedrock `eu-central-1` once real user PDFs enter
the pipeline. Same `InvoiceExtractor` protocol; only the client + the
model ID translation differ.

Bedrock requires fully-qualified model IDs with date stamps and version
suffix. The `eu.` prefix engages the EU **cross-region inference
profile**, which routes invocations across eu-central-1 / eu-west-1 /
eu-west-3 for capacity headroom. Newer Claude 4.x models are typically
only available via the inference profile, not via the bare regional ID.
"""

from __future__ import annotations

import anthropic

from src.pipeline.extraction.anthropic_provider import _call_claude, _load_prompt
from src.pipeline.extraction.provider import ExtractionResult

# Map our canonical short model names (used everywhere else in the code)
# to Bedrock's fully-qualified inference-profile IDs.
BEDROCK_MODEL_MAP: dict[str, str] = {
    "claude-haiku-4-5": "eu.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-sonnet-4-6": "eu.anthropic.claude-sonnet-4-6-20250929-v1:0",
}


class BedrockExtractor:
    """Anthropic-via-Bedrock implementation of InvoiceExtractor.

    AWS credentials are sourced from the standard boto3 credential chain
    (env vars `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for dev;
    instance profile / IRSA in prod). Region is configurable; spec
    pins this to `eu-central-1` for RODO/GDPR compliance.
    """

    def __init__(
        self,
        aws_region: str = "eu-central-1",
        aws_access_key: str | None = None,
        aws_secret_key: str | None = None,
    ) -> None:
        # Pass creds explicitly so we don't depend on boto3's default
        # credential chain inspecting OS env vars — pydantic-settings
        # loads .env into Settings without polluting os.environ.
        self._client = anthropic.AnthropicBedrock(
            aws_region=aws_region,
            aws_access_key=aws_access_key,
            aws_secret_key=aws_secret_key,
        )
        self._prompt = _load_prompt()

    def extract(self, images: list[bytes], model: str) -> ExtractionResult:
        bedrock_model_id = BEDROCK_MODEL_MAP.get(model)
        if bedrock_model_id is None:
            raise ValueError(
                f"No Bedrock model mapping for canonical name {model!r}. "
                f"Extend BEDROCK_MODEL_MAP in bedrock_provider.py."
            )
        # Report the canonical short name so persisted `extracted_model`
        # field stays stable across provider switches.
        return _call_claude(
            self._client,
            self._prompt,
            images,
            model=bedrock_model_id,
            reported_model=model,
        )
