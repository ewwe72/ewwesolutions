"""SIWZ attachment fetcher for the **logintrade.net** procurement platform.

Many Polish contracting authorities publish their tender pages on
subdomains of `logintrade.net` (e.g. `starostwo-puck.logintrade.net`,
`bip.<gmina>.logintrade.net`). Each tender lives at a stable URL of
the shape:

    https://<authority>.logintrade.net/zapytania_email,<id>,<token>.html

The page is a plain, server-side-rendered XHTML view ("Podgląd
zapytania") — no SPA, no JS-rendered widgets. Attachments (SIWZ +
załączniki) are listed as a flat table where each row contains:

  * a `<span class="attachment-name">…filename.ext</span>` element
  * an `<a href="DocumentService,getAttachmentUnlogged,<token>.html">`
    sibling/ancestor link

The `Unlogged` variant of the download endpoint is the public,
no-login path — calling it returns the binary file with a proper
`Content-Disposition: attachment; filename="…"` header that carries
the *original* filename (including Polish diacritics and spaces).

Discovery strategy
------------------
1. GET the tender URL once. If it 200s and contains at least one
   ``attachment-name`` span paired with a ``getAttachmentUnlogged``
   anchor, the SIWZ is **publicly downloadable** — proceed.
2. If the page redirects to a login form, or the file list only
   exposes ``getAttachmentLogged`` / form-gated links, the SIWZ is
   **gated**. We do NOT attempt to register, log in, or submit any
   form. Instead we capture whatever public preview info is visible
   (page HTML, listed filenames without download links) and write a
   ``PUBLIC_METADATA.md`` + ``manifest.json`` so the downstream
   pipeline can treat this tender as an "unfetchable SIWZ" case.

Filename source
---------------
The HTML span text and the `Content-Disposition` filename usually
agree. When they do not, the **HTTP header is authoritative** — the
platform's own choice for the user-facing filename, including
encoding nuances. Falls back to URL basename only if both are
absent (which has not been observed on logintrade so far).

Politeness
----------
* Custom User-Agent (`tender-agent/0.5 (+…)`) identifying us.
* 1 second sleep between requests.
* Exponential back-off on HTTP 429 (max 3 retries).
* Single shared `httpx.Client` to reuse the TCP connection + the
  `PHPSESSID` cookie the platform sets on first contact.

Honest failure mode
-------------------
Never fabricate placeholder PDFs. If a file is listed but the
download fails (403, 404, gated, server error), it is recorded in
the manifest with ``status: "failed"`` and an error string; the
caller can distinguish "real SIWZ on disk" from "we got nothing"
from "metadata only, file is gated".

CLI
---
    python -m tender_agent.siwz_logintrade <tender-url> --out <dir>

Public API
----------
    discover_attachments(tender_url) -> list[AttachmentRef]
    download_all(tender_url, dest_dir) -> Manifest
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup, Tag


USER_AGENT = "tender-agent/0.5 (+https://github.com/ewwe72/random)"
REQUEST_TIMEOUT = 30.0
INTER_REQUEST_SLEEP = 1.0
MAX_429_RETRIES = 3
CHUNK_SIZE = 64 * 1024

# Public, no-login download endpoint. The "Logged" variant requires a
# session cookie that we don't (and won't) acquire.
_UNLOGGED_HREF_RE = re.compile(r"DocumentService,getAttachmentUnlogged,", re.IGNORECASE)
_LOGGED_HREF_RE = re.compile(r"DocumentService,getAttachment(?!Unlogged)", re.IGNORECASE)
# Generic regex over a span text — file-extension sniff for sanity checks.
_FILE_EXT_RE = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|zip|rar|7z|txt|csv|odt|ods|xml)$", re.IGNORECASE)
# RFC 5987 / RFC 6266 filename parser. logintrade emits a simple form:
#   content-disposition: attachment; filename="Informacja z otwarcia ofert.pdf"
# We also tolerate `filename*=UTF-8''...` (rare here).
_CD_FILENAME_RE = re.compile(
    r"""filename\*?=(?:(?:UTF-8''([^;]+))|"([^"]+)"|([^;]+))""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AttachmentRef:
    """A single attachment as exposed by the tender page HTML."""

    display_name: str
    """Filename as printed in the `<span class="attachment-name">`."""

    href: Optional[str]
    """Absolute URL of the public download endpoint, or None if gated
    (page lists the filename without a usable download link)."""

    gated: bool
    """True iff the page lists this file but the download is gated
    behind a login / form submission."""


@dataclass
class DownloadResult:
    source_url: str
    display_name: str
    """Name as shown on the tender page."""
    saved_filename: Optional[str]
    """Final filename on disk (None if download failed or file gated)."""
    size_bytes: Optional[int]
    sha256: Optional[str]
    content_type: Optional[str]
    downloaded_at_utc: Optional[str]
    status: str
    """One of: 'ok', 'gated', 'failed'."""
    error: Optional[str] = None


@dataclass
class Manifest:
    tender_url: str
    discovered_at_utc: str
    n_attachments: int
    n_downloaded_ok: int
    n_gated: int
    n_failed: int
    results: list[DownloadResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "tender_url": self.tender_url,
            "discovered_at_utc": self.discovered_at_utc,
            "n_attachments": self.n_attachments,
            "n_downloaded_ok": self.n_downloaded_ok,
            "n_gated": self.n_gated,
            "n_failed": self.n_failed,
            "results": [
                {
                    "source_url": r.source_url,
                    "display_name": r.display_name,
                    "saved_filename": r.saved_filename,
                    "size_bytes": r.size_bytes,
                    "sha256": r.sha256,
                    "content_type": r.content_type,
                    "downloaded_at_utc": r.downloaded_at_utc,
                    "status": r.status,
                    "error": r.error,
                }
                for r in self.results
            ],
        }


def _new_client() -> httpx.Client:
    return httpx.Client(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "pl,en;q=0.7",
        },
    )


def _polite_get(
    client: httpx.Client,
    url: str,
    *,
    stream: bool = False,
) -> httpx.Response:
    """GET with retry on 429 and a 1s gap between requests.

    On 429 we back off 2s, 4s, 8s. On other errors we raise immediately —
    the platform is read-only public, so 4xx/5xx mean the resource is
    actually unavailable, not transiently rate-limited.
    """
    delay = 2.0
    for attempt in range(MAX_429_RETRIES + 1):
        if stream:
            ctx = client.stream("GET", url)
            response = ctx.__enter__()
            # Caller must close; but we need to inspect status first.
            if response.status_code == 429 and attempt < MAX_429_RETRIES:
                response.close()
                time.sleep(delay)
                delay *= 2
                continue
            return response
        response = client.get(url)
        if response.status_code == 429 and attempt < MAX_429_RETRIES:
            time.sleep(delay)
            delay *= 2
            continue
        return response
    raise RuntimeError("unreachable: retry loop fell through")


def _filename_from_content_disposition(header: Optional[str]) -> Optional[str]:
    """Parse `Content-Disposition`, preferring `filename*=UTF-8''…` then
    quoted `filename="…"` then bare `filename=…`.

    The RFC 5987 form is percent-decoded. Returns None if no filename
    can be found.
    """
    if not header:
        return None
    match = _CD_FILENAME_RE.search(header)
    if not match:
        return None
    utf8, quoted, bare = match.groups()
    raw = utf8 or quoted or bare
    if raw is None:
        return None
    if utf8:
        raw = unquote(raw)
    # Sanitise: no slashes/backslashes/NULs.
    raw = raw.strip().replace("/", "_").replace("\\", "_").replace("\x00", "")
    return raw or None


def _safe_filename(name: str) -> str:
    """Make a Polish-friendly filename safe for posix filesystems.

    Keeps spaces, diacritics, parens, hyphens; strips path separators
    and control characters. logintrade routinely emits names with
    spaces and Polish letters (e.g. `Ogłoszenie o zamówieniu.pdf`).
    """
    cleaned = "".join(ch for ch in name if ch.isprintable() and ch not in {"/", "\\", "\x00"})
    cleaned = cleaned.strip().strip(".")
    return cleaned or "attachment"


def _resolve(base: str, href: str) -> str:
    """Make `href` absolute against the tender page URL.

    `urljoin` handles both relative (`DocumentService,...`) and
    absolute (`https://…`) hrefs correctly here because the tender
    URL ends in `.html` (treated as a file in its parent dir).
    """
    return urljoin(base, href)


def discover_attachments(tender_url: str, *, client: Optional[httpx.Client] = None) -> list[AttachmentRef]:
    """Fetch the tender page and return all listed attachments.

    Each entry is either downloadable (``href`` set, ``gated=False``)
    or gated (``href=None``, ``gated=True``). The caller is responsible
    for distinguishing the two when materialising files to disk.

    Discovery walks the HTML in two passes:

    1. For every ``<span class="attachment-name">``, find the nearest
       ancestor that contains a ``getAttachmentUnlogged`` anchor.
       If found, the file is public.
    2. If the span is paired only with a logged-only / form-action
       link, treat it as gated.
    3. Bare ``getAttachmentUnlogged`` anchors that are NOT paired with
       a span are still reported using their URL basename as
       ``display_name`` (this has not been observed in practice but is
       a safe fallback).
    """
    owns_client = client is None
    client = client or _new_client()
    try:
        response = _polite_get(client, tender_url)
        response.raise_for_status()
        html = response.text
    finally:
        if owns_client:
            client.close()

    soup = BeautifulSoup(html, "lxml")
    refs: list[AttachmentRef] = []
    seen_hrefs: set[str] = set()

    for span in soup.find_all("span", class_="attachment-name"):
        if not isinstance(span, Tag):
            continue
        display = span.get_text(strip=True)
        if not display:
            continue

        # Walk up to find the row/container that holds the download anchor.
        anchor: Optional[Tag] = None
        gated = False
        container: Optional[Tag] = span
        for _ in range(8):
            if container is None:
                break
            for candidate in container.find_all("a", href=True):
                href_val = candidate.get("href", "")
                href_str_local = href_val if isinstance(href_val, str) else ""
                if _UNLOGGED_HREF_RE.search(href_str_local):
                    anchor = candidate
                    break
                if _LOGGED_HREF_RE.search(href_str_local):
                    gated = True
            if anchor is not None:
                break
            container = container.parent

        if anchor is not None:
            raw_href = anchor.get("href", "")
            href_str = raw_href if isinstance(raw_href, str) else ""
            absolute = _resolve(tender_url, href_str)
            if absolute in seen_hrefs:
                continue
            seen_hrefs.add(absolute)
            refs.append(AttachmentRef(display_name=display, href=absolute, gated=False))
        else:
            refs.append(AttachmentRef(display_name=display, href=None, gated=gated or True))

    # Fallback: orphan unlogged anchors with no span pairing.
    for anchor_tag in soup.find_all("a", href=True):
        if not isinstance(anchor_tag, Tag):
            continue
        href_val = anchor_tag.get("href", "")
        href_str = href_val if isinstance(href_val, str) else ""
        if not _UNLOGGED_HREF_RE.search(href_str):
            continue
        absolute = _resolve(tender_url, href_str)
        if absolute in seen_hrefs:
            continue
        text = anchor_tag.get_text(strip=True) or Path(urlparse(absolute).path).name
        if not _FILE_EXT_RE.search(text):
            # Probably navigation, not a file.
            continue
        seen_hrefs.add(absolute)
        refs.append(AttachmentRef(display_name=text, href=absolute, gated=False))

    return refs


def _download_one(
    client: httpx.Client,
    ref: AttachmentRef,
    dest_dir: Path,
) -> DownloadResult:
    """Stream one attachment to disk, computing sha256 on the fly.

    Returns a DownloadResult with status:
      * 'ok'     — file persisted, sha256 + size set
      * 'gated'  — ref.href is None or response indicates login wall
      * 'failed' — network/HTTP/IO error; partial file is removed
    """
    if ref.href is None or ref.gated:
        return DownloadResult(
            source_url="",
            display_name=ref.display_name,
            saved_filename=None,
            size_bytes=None,
            sha256=None,
            content_type=None,
            downloaded_at_utc=None,
            status="gated",
            error="Attachment listed on tender page but no public download link found",
        )

    try:
        with client.stream("GET", ref.href) as response:
            if response.status_code == 429:
                # Single retry handled here for streamed responses.
                response.close()
                time.sleep(4.0)
                response2 = client.stream("GET", ref.href).__enter__()
            else:
                response2 = response

            if response2.status_code >= 400:
                err = f"HTTP {response2.status_code} on {ref.href}"
                response2.close()
                return DownloadResult(
                    source_url=ref.href,
                    display_name=ref.display_name,
                    saved_filename=None,
                    size_bytes=None,
                    sha256=None,
                    content_type=None,
                    downloaded_at_utc=None,
                    status="failed",
                    error=err,
                )

            content_type = response2.headers.get("content-type")
            # An HTML response on a download endpoint means we hit a
            # login wall — record as gated, don't save the HTML body
            # as if it were a PDF.
            if content_type and "text/html" in content_type.lower():
                response2.close()
                return DownloadResult(
                    source_url=ref.href,
                    display_name=ref.display_name,
                    saved_filename=None,
                    size_bytes=None,
                    sha256=None,
                    content_type=content_type,
                    downloaded_at_utc=None,
                    status="gated",
                    error="Download endpoint returned HTML (likely a login wall)",
                )

            header_name = _filename_from_content_disposition(
                response2.headers.get("content-disposition")
            )
            chosen = header_name or ref.display_name or Path(urlparse(ref.href).path).name
            safe_name = _safe_filename(chosen)
            dest_path = dest_dir / safe_name

            hasher = hashlib.sha256()
            size = 0
            tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
            try:
                with open(tmp_path, "wb") as fh:
                    for chunk in response2.iter_bytes(CHUNK_SIZE):
                        if chunk:
                            fh.write(chunk)
                            hasher.update(chunk)
                            size += len(chunk)
                tmp_path.replace(dest_path)
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
            finally:
                response2.close()

            return DownloadResult(
                source_url=ref.href,
                display_name=ref.display_name,
                saved_filename=safe_name,
                size_bytes=size,
                sha256=hasher.hexdigest(),
                content_type=content_type,
                downloaded_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                status="ok",
            )
    except (httpx.HTTPError, OSError) as exc:
        return DownloadResult(
            source_url=ref.href or "",
            display_name=ref.display_name,
            saved_filename=None,
            size_bytes=None,
            sha256=None,
            content_type=None,
            downloaded_at_utc=None,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )


def _write_gated_metadata(
    dest_dir: Path,
    tender_url: str,
    refs: list[AttachmentRef],
    page_html: Optional[str],
) -> None:
    """When the SIWZ is gated, persist whatever public info we have.

    Writes ``PUBLIC_METADATA.md`` listing every filename that the page
    advertises and an explanation of the gating. The downstream
    pipeline reads this file to mark the tender as "unfetchable SIWZ".
    """
    md = dest_dir / "PUBLIC_METADATA.md"
    lines: list[str] = []
    lines.append("# Gated SIWZ — public metadata only")
    lines.append("")
    lines.append(f"- Source: {tender_url}")
    lines.append(f"- Captured at (UTC): {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("## Why this is metadata-only")
    lines.append("")
    lines.append(
        "The logintrade tender page lists attachments but the download "
        "endpoints require login / form submission. The fetcher will not "
        "create an account or submit forms (read-only, public-data only)."
    )
    lines.append("")
    lines.append("## Files visible on the public page")
    lines.append("")
    if refs:
        for ref in refs:
            marker = "downloadable" if ref.href and not ref.gated else "gated"
            lines.append(f"- `{ref.display_name}` — {marker}")
    else:
        lines.append("_None visible on the rendered page._")
    lines.append("")
    if page_html:
        lines.append("Raw HTML of the tender page saved alongside as `tender_page.html`.")
        (dest_dir / "tender_page.html").write_text(page_html, encoding="utf-8")
    md.write_text("\n".join(lines), encoding="utf-8")


def download_all(tender_url: str, dest_dir: Path) -> Manifest:
    """Discover + download every public attachment on a logintrade tender page.

    Creates ``dest_dir`` if missing, persists each attachment under its
    ``Content-Disposition`` filename, and writes
    ``dest_dir/manifest.json`` with provenance for the entire batch.

    If every advertised attachment is gated (no public download link
    on the rendered HTML), ``PUBLIC_METADATA.md`` + ``tender_page.html``
    are written instead of file blobs, and the returned manifest has
    ``n_downloaded_ok == 0`` and ``n_gated >= 1``.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    started_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with _new_client() as client:
        # Fetch tender HTML once so we can both parse it and persist it
        # in the gated-only branch.
        page_resp = _polite_get(client, tender_url)
        page_resp.raise_for_status()
        page_html = page_resp.text
        time.sleep(INTER_REQUEST_SLEEP)

        soup = BeautifulSoup(page_html, "lxml")
        refs: list[AttachmentRef] = []
        seen: set[str] = set()
        for span in soup.find_all("span", class_="attachment-name"):
            if not isinstance(span, Tag):
                continue
            display = span.get_text(strip=True)
            if not display:
                continue
            anchor: Optional[Tag] = None
            gated_only = False
            container: Optional[Tag] = span
            for _ in range(8):
                if container is None:
                    break
                for cand in container.find_all("a", href=True):
                    href_val = cand.get("href", "")
                    href_str = href_val if isinstance(href_val, str) else ""
                    if _UNLOGGED_HREF_RE.search(href_str):
                        anchor = cand
                        break
                    if _LOGGED_HREF_RE.search(href_str):
                        gated_only = True
                if anchor is not None:
                    break
                container = container.parent
            if anchor is not None:
                raw_href = anchor.get("href", "")
                href_str2 = raw_href if isinstance(raw_href, str) else ""
                absolute = _resolve(tender_url, href_str2)
                if absolute in seen:
                    continue
                seen.add(absolute)
                refs.append(AttachmentRef(display_name=display, href=absolute, gated=False))
            else:
                refs.append(AttachmentRef(display_name=display, href=None, gated=True or gated_only))

        manifest = Manifest(
            tender_url=tender_url,
            discovered_at_utc=started_utc,
            n_attachments=len(refs),
            n_downloaded_ok=0,
            n_gated=0,
            n_failed=0,
        )

        any_downloadable = any(r.href for r in refs if not r.gated)

        if not refs:
            # Nothing listed at all. Persist the page for forensic
            # inspection but don't claim a gated SIWZ — could be a
            # tender with no public docs yet.
            _write_gated_metadata(dest_dir, tender_url, refs, page_html)
        elif not any_downloadable:
            # All gated.
            _write_gated_metadata(dest_dir, tender_url, refs, page_html)
            for ref in refs:
                result = DownloadResult(
                    source_url="",
                    display_name=ref.display_name,
                    saved_filename=None,
                    size_bytes=None,
                    sha256=None,
                    content_type=None,
                    downloaded_at_utc=None,
                    status="gated",
                    error="Login required to download",
                )
                manifest.results.append(result)
                manifest.n_gated += 1
        else:
            for ref in refs:
                result = _download_one(client, ref, dest_dir)
                manifest.results.append(result)
                if result.status == "ok":
                    manifest.n_downloaded_ok += 1
                elif result.status == "gated":
                    manifest.n_gated += 1
                else:
                    manifest.n_failed += 1
                time.sleep(INTER_REQUEST_SLEEP)

    manifest_path = dest_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tender_agent.siwz_logintrade",
        description="Download SIWZ + attachments from a logintrade.net tender URL.",
    )
    parser.add_argument("tender_url", help="Full https://…logintrade.net/zapytania_email,…html URL")
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Destination directory. Created if missing. Files + manifest.json land here.",
    )
    args = parser.parse_args(argv)

    manifest = download_all(args.tender_url, args.out)
    summary = (
        f"discovered={manifest.n_attachments} "
        f"downloaded={manifest.n_downloaded_ok} "
        f"gated={manifest.n_gated} "
        f"failed={manifest.n_failed}"
    )
    sys.stdout.write(summary + "\n")
    for r in manifest.results:
        if r.status == "ok":
            sys.stdout.write(f"  [ok]     {r.saved_filename}  ({r.size_bytes} bytes)\n")
        elif r.status == "gated":
            sys.stdout.write(f"  [gated]  {r.display_name}\n")
        else:
            sys.stdout.write(f"  [failed] {r.display_name}: {r.error}\n")
    return 0 if (manifest.n_downloaded_ok > 0 or manifest.n_gated > 0) else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
