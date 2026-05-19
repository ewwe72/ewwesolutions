"""SIWZ + załączniki downloader for e-Zamówienia mp-client tenders.

The contracting-authority side of e-Zamówienia (``mp-client`` — "moduł
postępowania") ships every public proceeding's documents through a
read-only JSON API the Angular SPA itself calls. We bypass the SPA and
hit those endpoints directly.

Discovery strategy (re-confirm this if e-Zamówienia ships a new
frontend — files dumped to ``/tmp`` during the original probe are not
shipped with the repo):

1. The SPA shell at ``/mp-client/`` returns a 2.3 KB Angular index that
   loads runtime + chunked JS. Real config sits at
   ``/mp-web/api/v1/Config`` (200, JSON), which exposes
   ``apiUrlQuery: "/mp-readmodels/api"`` — the read-model base.
2. Lazy chunks (e.g. ``8-es2015.<hash>.js``) hard-code endpoint suffixes
   that the SPA composes with ``apiUrlQuery``. The relevant pair is::

       /mp-readmodels/api/Search/GetTender?id=<ocds-uuid>
       /mp-readmodels/api/Tender/DownloadDocument/<ocds-uuid>/<documentObjectId>

   Both are unauthenticated — the SPA only attaches a Bearer token when
   the user is logged in to manage *their own* proceeding. Public
   reading of someone else's tender works anonymously.
3. ``/mp-readmodels/api/Tender/DownloadAttachment?attachmentId=...`` (the
   sibling endpoint used to fetch ``attachment.uniqueAttachmentIdentifier``)
   **requires** a Bearer token. Don't use it — DownloadDocument is the
   public path.

What ``Search/GetTender`` returns (relevant fields only)::

    {
      "objectId": "ocds-148610-<uuid>",          # tender id (same as URL slug)
      "title": "<short>",
      "organizationName": "...",
      "referenceNumber": "ZP/35/2026/TP",
      "tenderDocuments": [
        {
          "objectId": "ocds-148610-<uuid>_3",    # document object id
          "name": "ZAŁ. NR 3 DO SWZ - OŚWIADCZENIE...",
          "tenderDocumentState": "Published" | "Deleted" | "Archived",
          "publishedDate": "2026-05-11T07:00:42.72Z",
          "attachment": {
            "fileName": "ZAŁ. NR 3 DO SWZ - OŚWIADCZENIE...docx",
            "mimeType": "application/vnd.openxmlformats-officedocument...",
            "uniqueAttachmentIdentifier": "<hex32>",
            "hash": "<sha256 hex>",
            "isDeleted": false,
            "fileSize": 67562
          },
          ...
        },
        ...
      ]
    }

We download every document where ``tenderDocumentState == "Published"``
and ``attachment.isDeleted is False``. ``Archived`` / ``Deleted`` items
(typically a re-uploaded SIWZ that superseded an older mistake) are
recorded in the manifest as ``skipped`` so it's clear we didn't lose
them — we just didn't fetch the bytes.

Filenames come from ``attachment.fileName`` in the JSON, which matches
the RFC 5987 ``Content-Disposition: filename*=UTF-8''...`` returned by
the download endpoint. We do not invent or sanitise names beyond
stripping path separators (a paranoid defence against a hypothetical
malicious upload — never observed in practice).
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


EZAMOWIENIA_HOST = "https://ezamowienia.gov.pl"
READMODELS_BASE = f"{EZAMOWIENIA_HOST}/mp-readmodels/api"
GET_TENDER_URL = f"{READMODELS_BASE}/Search/GetTender"
DOWNLOAD_DOCUMENT_URL = f"{READMODELS_BASE}/Tender/DownloadDocument"

USER_AGENT = "tender-agent/0.5 (+https://github.com/ewwe72/random)"
DEFAULT_TIMEOUT = 60.0  # SWZ PDFs are typically 200 KB – 5 MB; be generous.
DEFAULT_SLEEP_SECONDS = 1.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# OCDS tender id as used by the SPA URL: `ocds-148610-<uuid>`.
_OCDS_RE = re.compile(r"ocds-148610-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class SiwzDiscoveryError(RuntimeError):
    """Raised when we can't reach the e-Zamówienia API or interpret its reply.

    Distinct from ``httpx.HTTPError`` so callers can treat "site changed
    its frontend" differently from "transient network failure". If you
    catch this, the right next step is human re-inspection of the
    discovery strategy at the top of this module.
    """


class SiwzDownloadError(RuntimeError):
    """Raised when a document listed in the manifest fails to download.

    Never silently substituted with an empty file or a placeholder — the
    pipeline must be able to distinguish "we got the real SIWZ" from
    "we got nothing". Catch + report; do not swallow.
    """


@dataclasses.dataclass(frozen=True)
class AttachmentRef:
    """One downloadable document attached to a tender.

    Mirrors the subset of fields the SPA's ``tenderDocuments[]`` array
    uses to render the attachment list. ``document_object_id`` is the
    address we POST to ``DownloadDocument`` with; ``unique_attachment_id``
    is recorded for traceability but is **not** used as a URL parameter
    (DownloadAttachment is auth-gated; see module docstring).
    """

    tender_id: str
    document_object_id: str
    display_name: str
    file_name: str
    mime_type: str | None
    size_bytes: int | None
    expected_sha256: str | None
    published_date: str | None
    state: str
    is_deleted: bool
    unique_attachment_id: str | None

    @property
    def download_url(self) -> str:
        return f"{DOWNLOAD_DOCUMENT_URL}/{self.tender_id}/{self.document_object_id}"

    @property
    def is_downloadable(self) -> bool:
        """True for ``Published`` documents whose attachment isn't tombstoned."""
        return self.state == "Published" and not self.is_deleted


@dataclasses.dataclass
class DownloadedFile:
    """One file we successfully wrote to disk."""

    source_url: str
    file_name: str
    saved_path: Path
    size_bytes: int
    sha256: str
    content_type: str | None
    downloaded_at_utc: str
    expected_sha256_match: bool | None  # None when the API didn't expose a hash


@dataclasses.dataclass
class SkippedFile:
    """One document we deliberately did not fetch (typically Archived/Deleted)."""

    source_url: str
    file_name: str
    state: str
    is_deleted: bool
    reason: str


@dataclasses.dataclass
class Manifest:
    """The on-disk record of what was downloaded for one tender."""

    tender_id: str
    tender_url: str
    title: str | None
    organization_name: str | None
    reference_number: str | None
    fetched_at_utc: str
    downloaded: list[DownloadedFile]
    skipped: list[SkippedFile]

    def to_json(self) -> str:
        def _dump(x: Any) -> Any:
            if isinstance(x, Path):
                return str(x)
            if dataclasses.is_dataclass(x) and not isinstance(x, type):
                return {k: _dump(v) for k, v in dataclasses.asdict(x).items()}
            if isinstance(x, list):
                return [_dump(v) for v in x]
            if isinstance(x, dict):
                return {k: _dump(v) for k, v in x.items()}
            return x

        return json.dumps(_dump(self), ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def extract_tender_id(tender_url: str) -> str:
    """Pull the `ocds-148610-<uuid>` slug out of a tender URL.

    Accepts both ``/mp-client/search/list/<ocds>`` and
    ``/mp-client/tenders/<ocds>`` shapes (both observed in BZP HTML
    bodies pointing at the procurement-side platform). Also accepts a
    bare ocds id.

    Raises ``ValueError`` when no ocds-148610 id can be found, so the
    caller learns about a malformed URL immediately instead of probing
    a nonsense endpoint.
    """
    m = _OCDS_RE.search(tender_url)
    if not m:
        raise ValueError(
            f"No `ocds-148610-<uuid>` tender id found in {tender_url!r}. "
            "Expected an e-Zamówienia mp-client URL like "
            "`https://ezamowienia.gov.pl/mp-client/search/list/ocds-148610-...`."
        )
    return m.group(0)


def _safe_filename(raw: str) -> str:
    """Strip path separators from a platform-provided filename.

    e-Zamówienia controls these filenames so injection is unlikely, but
    we still refuse to write outside the destination dir if a future
    upload contains `/` or `..`. Preserves Polish diacritics.
    """
    cleaned = raw.replace("\\", "/").split("/")[-1].strip()
    cleaned = cleaned.replace("..", "").strip()
    if not cleaned:
        raise ValueError(f"Empty filename after sanitisation: {raw!r}")
    return cleaned


def _http_client(timeout: float) -> httpx.Client:
    """One place to construct the httpx client.

    Polite ``User-Agent`` so the operator can be identified in the
    e-Zamówienia access logs if needed. No cookies, no credentials.
    """
    return httpx.Client(
        timeout=timeout,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json, application/octet-stream",
            "Accept-Language": "pl,en;q=0.5",
        },
        follow_redirects=True,
    )


def _get_with_backoff(
    client: httpx.Client,
    url: str,
    *,
    max_retries: int = 3,
    backoff_base: float = 2.0,
) -> httpx.Response:
    """GET with naïve exponential backoff on retryable statuses + transport errors.

    Doesn't try to honour ``Retry-After`` precisely — the worst case on
    this read-only public API is a one-off 502 from the upstream
    .NET host; a few retries with 2/4/8s sleeps cover it.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.get(url)
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt == max_retries:
                raise
            time.sleep(backoff_base ** attempt)
            continue
        if response.status_code in RETRYABLE_STATUS and attempt < max_retries:
            time.sleep(backoff_base ** attempt)
            continue
        return response
    # Should be unreachable — both branches above either return or raise.
    raise SiwzDiscoveryError(f"GET {url} exhausted retries") from last_exc


def discover_attachments(
    tender_url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[dict[str, Any], list[AttachmentRef]]:
    """Hit ``/Search/GetTender`` and turn its response into AttachmentRefs.

    Returns ``(raw_tender_json, attachments)``. The raw JSON is handed
    back so the caller can write tender metadata into the manifest
    without re-fetching.
    """
    tender_id = extract_tender_id(tender_url)
    api_url = f"{GET_TENDER_URL}?id={tender_id}"
    with _http_client(timeout) as client:
        response = _get_with_backoff(client, api_url)
        if response.status_code != 200:
            raise SiwzDiscoveryError(
                f"GET {api_url} returned HTTP {response.status_code}. "
                "Either the tender id is wrong or e-Zamówienia changed the "
                "Search/GetTender contract — re-confirm the discovery "
                "strategy in this module's docstring."
            )
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise SiwzDiscoveryError(
                f"Search/GetTender returned non-JSON for {tender_id}: "
                f"{response.text[:200]!r}"
            ) from exc

    if not isinstance(data, dict):
        raise SiwzDiscoveryError(
            f"Search/GetTender returned a {type(data).__name__}, expected object."
        )

    documents = data.get("tenderDocuments")
    if not isinstance(documents, list):
        raise SiwzDiscoveryError(
            f"Search/GetTender for {tender_id} has no `tenderDocuments` array. "
            "Has e-Zamówienia renamed the field?"
        )

    refs: list[AttachmentRef] = []
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        attachment = doc.get("attachment") or {}
        file_name_raw = (attachment.get("fileName") or "").strip()
        if not file_name_raw:
            # No attachment payload (e.g. external `url` reference). Skip.
            continue
        document_object_id = doc.get("objectId")
        if not isinstance(document_object_id, str) or not document_object_id:
            continue
        refs.append(
            AttachmentRef(
                tender_id=tender_id,
                document_object_id=document_object_id,
                display_name=str(doc.get("name") or file_name_raw),
                file_name=file_name_raw,
                mime_type=attachment.get("mimeType"),
                size_bytes=attachment.get("fileSize")
                if isinstance(attachment.get("fileSize"), int)
                else None,
                expected_sha256=attachment.get("hash") if isinstance(attachment.get("hash"), str) else None,
                published_date=doc.get("publishedDate") if isinstance(doc.get("publishedDate"), str) else None,
                state=str(doc.get("tenderDocumentState") or "Unknown"),
                is_deleted=bool(attachment.get("isDeleted")),
                unique_attachment_id=attachment.get("uniqueAttachmentIdentifier")
                if isinstance(attachment.get("uniqueAttachmentIdentifier"), str)
                else None,
            )
        )

    return data, refs


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def _disambiguate(dest_dir: Path, name: str, taken: set[str]) -> str:
    """Return a filename that doesn't collide with one already written.

    e-Zamówienia regularly uploads two files with the same display name
    (e.g. one "Pytania i odpowiedzi" Q&A doc per round). We keep the
    original stem and insert `__N` before the extension on collision —
    `foo.docx` → `foo__2.docx` → `foo__3.docx`, etc.
    """
    if name not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    i = 2
    while True:
        candidate = f"{stem}__{i}{('.' + ext) if ext else ''}"
        if candidate not in taken:
            return candidate
        i += 1


def _download_one(
    client: httpx.Client,
    ref: AttachmentRef,
    dest_dir: Path,
    taken: set[str],
) -> DownloadedFile:
    """Stream one attachment to disk + verify against the JSON-declared hash."""
    safe_name = _disambiguate(dest_dir, _safe_filename(ref.file_name), taken)
    target_path = dest_dir / safe_name

    response = _get_with_backoff(client, ref.download_url)
    if response.status_code != 200:
        raise SiwzDownloadError(
            f"Download for {ref.display_name!r} ({ref.download_url}) "
            f"returned HTTP {response.status_code}."
        )
    body = response.content
    if not body:
        raise SiwzDownloadError(
            f"Empty body downloading {ref.display_name!r} from {ref.download_url}."
        )

    sha256 = hashlib.sha256(body).hexdigest()
    target_path.write_bytes(body)

    expected_match: bool | None
    if ref.expected_sha256:
        expected_match = sha256.lower() == ref.expected_sha256.lower()
        if not expected_match:
            raise SiwzDownloadError(
                f"sha256 mismatch for {ref.file_name!r}: "
                f"got {sha256}, expected {ref.expected_sha256} "
                f"(URL {ref.download_url}). File was written to "
                f"{target_path} for inspection, but treat the manifest "
                "as untrusted."
            )
    else:
        expected_match = None

    return DownloadedFile(
        source_url=ref.download_url,
        file_name=safe_name,
        saved_path=target_path,
        size_bytes=len(body),
        sha256=sha256,
        content_type=response.headers.get("content-type"),
        downloaded_at_utc=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        expected_sha256_match=expected_match,
    )


def download_all(
    tender_url: str,
    dest_dir: Path,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    sleep_between: float = DEFAULT_SLEEP_SECONDS,
    include_archived: bool = False,
) -> Manifest:
    """Discover + download all SIWZ attachments for one tender.

    Writes one file per Published, non-deleted attachment into
    ``dest_dir`` (created if missing) and a ``manifest.json`` next to
    them. Returns the in-memory ``Manifest`` for caller use.

    ``include_archived``: e-Zamówienia keeps the bytes for ``Archived``
    documents around (you can still see them in the JSON), but its UI
    hides them — typically a SIWZ that was replaced by a corrected
    version. Default False: we follow the UI's behaviour and only
    download what the contracting authority intends bidders to read.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    tender_payload, refs = discover_attachments(tender_url, timeout=timeout)
    tender_id = extract_tender_id(tender_url)

    downloaded: list[DownloadedFile] = []
    skipped: list[SkippedFile] = []
    taken: set[str] = set()

    with _http_client(timeout) as client:
        for i, ref in enumerate(refs):
            wanted = ref.is_downloadable or (include_archived and not ref.is_deleted)
            if not wanted:
                reason = (
                    "tenderDocumentState=Deleted/Archived"
                    if ref.state != "Published"
                    else "attachment.isDeleted=true"
                )
                skipped.append(
                    SkippedFile(
                        source_url=ref.download_url,
                        file_name=ref.file_name,
                        state=ref.state,
                        is_deleted=ref.is_deleted,
                        reason=reason,
                    )
                )
                continue
            if i > 0 and sleep_between > 0:
                time.sleep(sleep_between)
            written = _download_one(client, ref, dest_dir, taken)
            taken.add(written.file_name)
            downloaded.append(written)

    manifest = Manifest(
        tender_id=tender_id,
        tender_url=tender_url,
        title=tender_payload.get("title") if isinstance(tender_payload.get("title"), str) else None,
        organization_name=tender_payload.get("organizationName")
        if isinstance(tender_payload.get("organizationName"), str)
        else None,
        reference_number=tender_payload.get("referenceNumber")
        if isinstance(tender_payload.get("referenceNumber"), str)
        else None,
        fetched_at_utc=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        downloaded=downloaded,
        skipped=skipped,
    )

    (dest_dir / "manifest.json").write_text(manifest.to_json(), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tender_agent.siwz_ezamowienia",
        description=(
            "Download the SIWZ + załączniki for one e-Zamówienia tender. "
            "Pass a tender URL (anything containing `ocds-148610-<uuid>`) "
            "and an output directory."
        ),
    )
    parser.add_argument(
        "tender_url",
        help=(
            "e-Zamówienia tender URL, e.g. "
            "https://ezamowienia.gov.pl/mp-client/search/list/ocds-148610-<uuid>"
        ),
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Destination directory. Will be created if missing.",
    )
    parser.add_argument(
        "--include-archived",
        action="store_true",
        help=(
            "Also download Archived documents (default: only Published). "
            "Archived = superseded by a newer upload; bidders normally "
            "shouldn't read these."
        ),
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Seconds between downloads (default: {DEFAULT_SLEEP_SECONDS}).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        manifest = download_all(
            args.tender_url,
            args.out,
            sleep_between=args.sleep,
            include_archived=args.include_archived,
        )
    except (SiwzDiscoveryError, SiwzDownloadError, ValueError) as exc:
        print(f"[siwz_ezamowienia] FAILED: {exc}", file=sys.stderr)
        return 2
    print(
        f"[siwz_ezamowienia] {manifest.tender_id}: "
        f"{len(manifest.downloaded)} file(s) saved to {args.out}, "
        f"{len(manifest.skipped)} skipped.",
        file=sys.stderr,
    )
    for d in manifest.downloaded:
        print(f"  + {d.file_name} ({d.size_bytes} B, sha256={d.sha256[:12]}...)", file=sys.stderr)
    for s in manifest.skipped:
        print(f"  - {s.file_name} [{s.reason}]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
