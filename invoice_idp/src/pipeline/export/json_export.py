"""JSON export — a clean dump of `CanonicalInvoice` for downstream consumers.

Strips extraction-time telemetry (`overall_confidence`,
`extraction_warnings`, `source_pdf_id`, `extracted_at`, `extracted_model`,
`extraction_version`) and per-field `confidence` dicts on
`Counterparty` / `LineItem`. After operator review those scores are
stale anyway, and accountants importing the JSON have no use for them.

Output is UTF-8 bytes; the endpoint sets `Content-Type: application/json`
and a filename derived from `invoice_number`.
"""

from __future__ import annotations

import json
from typing import Any

from src.models.invoice import CanonicalInvoice

_TELEMETRY_FIELDS: frozenset[str] = frozenset({
    "overall_confidence",
    "extraction_warnings",
    "source_pdf_id",
    "extracted_at",
    "extracted_model",
    "extraction_version",
})


def _strip_confidence(obj: Any) -> Any:
    """Recursively drop `confidence: {...}` keys from dicts/lists."""
    if isinstance(obj, dict):
        return {k: _strip_confidence(v) for k, v in obj.items() if k != "confidence"}
    if isinstance(obj, list):
        return [_strip_confidence(item) for item in obj]
    return obj


def to_export_dict(invoice: CanonicalInvoice) -> dict[str, Any]:
    """Return the JSON-safe export dict (telemetry + confidence stripped)."""
    full = invoice.model_dump(mode="json")
    pruned = {k: v for k, v in full.items() if k not in _TELEMETRY_FIELDS}
    return _strip_confidence(pruned)  # type: ignore[no-any-return]


def to_bytes(invoice: CanonicalInvoice) -> bytes:
    """Pretty-printed UTF-8 JSON."""
    return json.dumps(
        to_export_dict(invoice),
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    ).encode("utf-8")
