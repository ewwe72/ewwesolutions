"""Unit tests for the shared upload validation helpers.

Covers `src.app.services.invoice_upload` — the pure classifier that
both the JSON `POST /api/v1/invoices` and the HTML `POST /app/wgraj`
delegate to. These tests have no DB / network / framework deps:
input bytes in, categorical enum (or `None`) out.

The integration tests in `tests/integration/test_upload_validation.py`
still cover the end-to-end Polish copy + 400 wiring; this file is the
fast unit-level fence around the rules themselves.
"""

from __future__ import annotations

from src.app.services.invoice_upload import (
    PDF_MAGIC_BYTES,
    UploadIssue,
    classify_pdf_upload,
    is_pdf_bytes,
)


def test_pdf_magic_bytes_constant() -> None:
    """The constant is the literal four-byte signature plus the dash.

    Locking the value down here so a careless rename later doesn't
    silently break magic-byte detection on every uploaded invoice.
    """
    assert PDF_MAGIC_BYTES == b"%PDF-"


def test_is_pdf_bytes_accepts_real_header() -> None:
    assert is_pdf_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n") is True


def test_is_pdf_bytes_rejects_jpeg_header() -> None:
    """JPEG SOI + JFIF marker — common false-positive (user picked
    a photo instead of a PDF)."""
    assert is_pdf_bytes(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01") is False


def test_is_pdf_bytes_rejects_empty() -> None:
    assert is_pdf_bytes(b"") is False


def test_classify_returns_none_for_valid_small_pdf() -> None:
    """Happy path: tiny but well-formed-looking body under the cap."""
    content = b"%PDF-1.4\n" + b"X" * 100
    assert classify_pdf_upload(content, max_bytes=1024) is None


def test_classify_flags_empty_first() -> None:
    """Empty bodies must be classified as EMPTY, not NOT_PDF, even
    though they also fail the magic check. The empty message is more
    actionable for the user ('your file was 0 bytes' vs 'not a PDF')."""
    assert classify_pdf_upload(b"", max_bytes=1024) is UploadIssue.EMPTY


def test_classify_flags_oversize_before_magic_check() -> None:
    """A non-PDF body that also exceeds the cap reports TOO_LARGE,
    matching the branch order in both callers (`upload_invoice`
    raises 413 here; `upload_submit` shows the Polish size message)."""
    content = b"NOT_A_PDF_HEADER" + b"X" * 2048
    assert (
        classify_pdf_upload(content, max_bytes=1024)
        is UploadIssue.TOO_LARGE
    )


def test_classify_flags_not_pdf_when_size_ok() -> None:
    """Body within the cap but missing the magic header → NOT_PDF."""
    assert (
        classify_pdf_upload(b"plain text invoice", max_bytes=1024)
        is UploadIssue.NOT_PDF
    )


def test_classify_at_exact_cap_is_ok() -> None:
    """Boundary: content of exactly `max_bytes` should pass the size
    check (strict `>` in the helper, not `>=`). Caps are
    advertised to users as inclusive."""
    content = b"%PDF-" + b"X" * (1024 - len(b"%PDF-"))
    assert len(content) == 1024
    assert classify_pdf_upload(content, max_bytes=1024) is None


def test_classify_one_byte_over_cap_is_too_large() -> None:
    """Boundary: one byte past the cap trips TOO_LARGE."""
    content = b"%PDF-" + b"X" * (1024 - len(b"%PDF-") + 1)
    assert len(content) == 1025
    assert (
        classify_pdf_upload(content, max_bytes=1024)
        is UploadIssue.TOO_LARGE
    )


def test_upload_issue_values_are_stringly_typed() -> None:
    """`UploadIssue` mixes `str` so callers can drop the values into
    audit-event payloads without an explicit `.value` step."""
    assert UploadIssue.EMPTY == "empty"
    assert UploadIssue.TOO_LARGE == "too_large"
    assert UploadIssue.NOT_PDF == "not_pdf"
