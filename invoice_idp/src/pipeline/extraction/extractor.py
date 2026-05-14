"""Top-level invoice extraction — Haiku-only by default.

Routing logic (SPEC §6, v1.3):
  - `extract_from_pdf` runs Haiku 4.5 once. The editable review page in
    Phase 4 is the correction layer for low-confidence output.
  - `extract_from_pdf_force(model)` runs a specific model directly,
    powering the manual "Re-ekstrakcja (Sonnet)" button on the review
    page when the operator decides Haiku output isn't worth correcting
    by hand.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from src.app.config import get_settings
from src.models.invoice import CanonicalInvoice
from src.pipeline.extraction.anthropic_provider import AnthropicExtractor
from src.pipeline.extraction.bedrock_provider import BedrockExtractor
from src.pipeline.extraction.pdf import pdf_to_png_bytes
from src.pipeline.extraction.provider import ExtractionResult, InvoiceExtractor
from src.pipeline.validation.checks import compute_overall_confidence, validate

HAIKU_MODEL = "claude-haiku-4-5"
SONNET_MODEL = "claude-sonnet-4-6"
EXTRACTION_VERSION = "v1.1"

# Strings models occasionally emit instead of null when a nullable field
# is absent on the document. Treated as null for nullable date/string fields.
_UNKNOWN_STRINGS: frozenset[str] = frozenset({
    "<unknown>", "unknown", "n/a", "na", "brak", "?", "-",
})

# Names of CanonicalInvoice fields that accept null. Used only to decide
# whether an unknown-marker string should become null or be left as-is.
_NULLABLE_KEYS: frozenset[str] = frozenset({
    "sale_date", "place_of_issue", "notes",
    "nip", "regon", "address_line1", "address_line2",
    "postal_code", "city", "bank_account",
    "method", "due_date", "paid_date",
})

logger = logging.getLogger(__name__)


def get_extractor() -> InvoiceExtractor:
    """Return the configured extractor — Bedrock if AWS creds set, else direct Anthropic.

    Per SPEC §17 decision 8: Phase 1 uses direct Anthropic API on
    operator-supplied test PDFs (US data flow acceptable). Phase 3+
    must use AWS Bedrock `eu-central-1` for EU residency once real
    user invoices enter the pipeline. The provider switch is driven
    entirely by env vars — code stays identical.
    """
    settings = get_settings()
    if (
        settings.aws_access_key_id
        and settings.aws_secret_access_key
        and settings.aws_region
    ):
        logger.info("extractor: AWS Bedrock (region=%s)", settings.aws_region)
        return BedrockExtractor(
            aws_region=settings.aws_region,
            aws_access_key=settings.aws_access_key_id,
            aws_secret_key=settings.aws_secret_access_key,
        )
    if settings.anthropic_api_key:
        logger.info("extractor: direct Anthropic API")
        return AnthropicExtractor(api_key=settings.anthropic_api_key)
    raise RuntimeError(
        "No LLM provider configured. Set AWS_ACCESS_KEY_ID / "
        "AWS_SECRET_ACCESS_KEY / AWS_REGION for Bedrock, or "
        "ANTHROPIC_API_KEY for the direct API."
    )


def _normalize_extraction(data: dict[str, Any]) -> dict[str, Any]:
    """Pre-process raw LLM output so common model quirks don't trip Pydantic.

    Two transformations:
      * Strings like "<UNKNOWN>" / "N/A" in nullable fields become null.
      * Money amounts are rounded to 2 decimal places (some invoices have
        unit prices like 0.2411 PLN/unit that exceed Money's max precision;
        we round-and-record-warning rather than fail extraction).
    """
    def fix(obj: Any, key: str | None = None) -> Any:
        if isinstance(obj, dict):
            cleaned: dict[str, Any] = {}
            for k, v in obj.items():
                if isinstance(v, str) and v.strip().lower() in _UNKNOWN_STRINGS:
                    cleaned[k] = None if k in _NULLABLE_KEYS else v
                else:
                    cleaned[k] = fix(v, k)
            if "amount" in cleaned and "currency" in cleaned:
                amount = cleaned["amount"]
                if isinstance(amount, (int, float)):
                    cleaned["amount"] = round(float(amount), 2)
            return cleaned
        if isinstance(obj, list):
            return [fix(item) for item in obj]
        return obj

    result = fix(data)
    return result if isinstance(result, dict) else data


@dataclass(frozen=True)
class ExtractionRun:
    """Telemetry for one PDF extraction — what was tried, what won, cost."""
    pdf_path: Path
    invoice: CanonicalInvoice
    path_taken: str
    haiku_confidence: float
    sonnet_confidence: float | None
    haiku_input_tokens: int
    haiku_output_tokens: int
    sonnet_input_tokens: int          # 0 when Sonnet wasn't called
    sonnet_output_tokens: int

    @property
    def total_input_tokens(self) -> int:
        return self.haiku_input_tokens + self.sonnet_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self.haiku_output_tokens + self.sonnet_output_tokens


@dataclass(frozen=True)
class _Attempt:
    invoice: CanonicalInvoice | None
    hard_count: int
    soft_count: int
    confidence: float
    raw: ExtractionResult


def _collect_field_confidences(data: dict[str, Any]) -> dict[str, float]:
    flat: dict[str, float] = {}
    for role in ("seller", "buyer"):
        cp = data.get(role) or {}
        for field, score in (cp.get("confidence") or {}).items():
            try:
                flat[f"{role}.{field}"] = float(score)
            except (TypeError, ValueError):
                continue
    for idx, line in enumerate(data.get("lines") or []):
        for field, score in (line.get("confidence") or {}).items():
            try:
                flat[f"line[{idx}].{field}"] = float(score)
            except (TypeError, ValueError):
                continue
    return flat


def _build_invoice(
    raw: ExtractionResult, source_pdf_id: str
) -> tuple[CanonicalInvoice, list[str], list[str], float]:
    clean = _normalize_extraction(raw.data)
    invoice = CanonicalInvoice.model_validate({
        **clean,
        "source_pdf_id": source_pdf_id,
        "extracted_at": datetime.now(timezone.utc),
        "extracted_model": raw.model,
        "extraction_version": EXTRACTION_VERSION,
    })
    hard, soft = validate(invoice)
    confidences = _collect_field_confidences(raw.data)
    overall = compute_overall_confidence(confidences, len(hard), len(soft))
    invoice = invoice.model_copy(update={
        "overall_confidence": overall,
        "extraction_warnings": hard + [f"(soft) {w}" for w in soft],
    })
    return invoice, hard, soft, overall


def _attempt(
    images: list[bytes],
    extractor: InvoiceExtractor,
    model: str,
    source_pdf_id: str,
) -> _Attempt:
    raw = extractor.extract(images, model)
    try:
        invoice, hard, soft, conf = _build_invoice(raw, source_pdf_id)
        return _Attempt(invoice, len(hard), len(soft), conf, raw)
    except ValidationError as e:
        logger.warning("Pydantic validation failed for %s on %s: %s", model, source_pdf_id, e)
        return _Attempt(None, 999, 0, 0.0, raw)


def extract_from_pdf_force(
    pdf_path: Path, extractor: InvoiceExtractor, model: str
) -> ExtractionRun:
    """Re-extract a PDF using a specific model, bypassing routing.

    Used by the "Re-ekstrakcja" button on the review page: when Haiku-only
    extraction was wrong, the operator can spend the Sonnet quota to retry
    on the stronger model. Telemetry treats the forced model as if it were
    the only attempt (the other model's confidence/tokens stay zero).
    """
    images = pdf_to_png_bytes(pdf_path)
    source_pdf_id = pdf_path.name
    attempt = _attempt(images, extractor, model, source_pdf_id)
    if attempt.invoice is None:
        raise RuntimeError(
            f"Forced re-extraction with {model} failed to parse {pdf_path.name}"
        )

    is_sonnet = model == SONNET_MODEL
    return ExtractionRun(
        pdf_path=pdf_path,
        invoice=attempt.invoice,
        path_taken=f"forced-{model}",
        haiku_confidence=0.0 if is_sonnet else attempt.confidence,
        sonnet_confidence=attempt.confidence if is_sonnet else None,
        haiku_input_tokens=0 if is_sonnet else attempt.raw.input_tokens,
        haiku_output_tokens=0 if is_sonnet else attempt.raw.output_tokens,
        sonnet_input_tokens=attempt.raw.input_tokens if is_sonnet else 0,
        sonnet_output_tokens=attempt.raw.output_tokens if is_sonnet else 0,
    )


def extract_from_pdf(pdf_path: Path, extractor: InvoiceExtractor) -> ExtractionRun:
    """Extract one PDF with Haiku only.

    Per SPEC v1.3 §6 (the cost pivot): Sonnet auto-fallback is dropped.
    Haiku-only ≈ $0.004/invoice vs Haiku+Sonnet ≈ $0.011–0.025; the Phase
    4 editable review page is now the correction layer for low-confidence
    Haiku output. Operators can still spend budget on Sonnet by clicking
    "Re-ekstrakcja (Sonnet)" — `extract_from_pdf_force(model=SONNET_MODEL)`
    is the route the button takes.
    """
    images = pdf_to_png_bytes(pdf_path)
    source_pdf_id = pdf_path.name

    haiku = _attempt(images, extractor, HAIKU_MODEL, source_pdf_id)
    if haiku.invoice is None:
        raise RuntimeError(
            f"Haiku failed to parse a valid invoice from {pdf_path.name}; "
            f"operator may retry with Sonnet via the review page."
        )

    return ExtractionRun(
        pdf_path=pdf_path,
        invoice=haiku.invoice,
        path_taken="haiku-only",
        haiku_confidence=haiku.confidence,
        sonnet_confidence=None,
        haiku_input_tokens=haiku.raw.input_tokens,
        haiku_output_tokens=haiku.raw.output_tokens,
        sonnet_input_tokens=0,
        sonnet_output_tokens=0,
    )
