"""Shared PDF upload validation primitives.

Both `POST /api/v1/invoices` (JSON) and `POST /app/wgraj` (HTML form)
guard the same three failure modes after they have read the upload
bytes into memory:

  1. zero-length body
  2. body larger than the per-deploy `max_upload_mb` cap
  3. body that doesn't start with the `%PDF-` magic prefix

This module exposes the constant + a single pure classifier so the
two routes stay in lockstep on *what* counts as invalid. They keep
ownership of *how* to surface the error (HTTP status, copy language)
because the API returns English `HTTPException` with a 413 for
oversize while the web flow re-renders the upload template in Polish
with a 400 across the board.

No DB / IO / framework deps live here on purpose — see
`src/app/services/__init__.py` docstring.
"""

from __future__ import annotations

from enum import Enum

# Canonical PDF file signature. Kept here so the API router and the
# web router import from the same source rather than duplicating the
# literal.
PDF_MAGIC_BYTES = b"%PDF-"


class UploadIssue(str, Enum):
    """Categorical result of `classify_pdf_upload`.

    `str` mix-in keeps the values comparable / loggable without an
    explicit `.value` dance — handy for audit-event payloads and
    test assertions.
    """

    EMPTY = "empty"
    TOO_LARGE = "too_large"
    NOT_PDF = "not_pdf"


def is_pdf_bytes(content: bytes) -> bool:
    """True iff `content` starts with the PDF magic header.

    Pure check on the first few bytes — doesn't parse structure.
    A truncated or corrupt PDF that *starts* with `%PDF-` will pass;
    that's intentional: the worker has the real parser (pymupdf) and
    will surface a structured extraction error if the body is junk.
    """
    return content.startswith(PDF_MAGIC_BYTES)


def classify_pdf_upload(content: bytes, max_bytes: int) -> UploadIssue | None:
    """Return the first failing guard, or `None` if the upload is OK.

    Order matters: empty > oversize > wrong-magic, matching the
    existing branch order in both callers so error precedence is
    stable across the JSON and HTML surfaces. A zero-byte upload is
    classified as `EMPTY` even though it also fails the magic check;
    that gives the user the more actionable message.

    `max_bytes` is the absolute byte cap (caller multiplies MB → B),
    not the MB number — keeps this helper unaware of the
    `max_upload_mb` setting key.
    """
    if len(content) == 0:
        return UploadIssue.EMPTY
    if len(content) > max_bytes:
        return UploadIssue.TOO_LARGE
    if not is_pdf_bytes(content):
        return UploadIssue.NOT_PDF
    return None
