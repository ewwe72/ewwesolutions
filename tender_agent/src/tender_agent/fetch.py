"""BZP / e-Zamówienia fetcher.

Uses the public, no-auth `mo-board/api/v1/notice` endpoint at
ezamowienia.gov.pl. Discovered via API probing 2026-05-14:

  https://ezamowienia.gov.pl/mo-board/api/v1/notice
    ?PageSize=<int>
    &NoticeType=ContractNotice
    &PublicationDateFrom=YYYY-MM-DD
    &PublicationDateTo=YYYY-MM-DD

The endpoint returns an array of announcement objects. Each object has
the structured metadata fields **plus** the full HTML body of the
notice embedded in `htmlBody`. We cache each fetched announcement in
`_samples/<bzp-id-flat>/` so subsequent parse/draft runs avoid hitting
the API.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import httpx

BZP_API = "https://ezamowienia.gov.pl/mo-board/api/v1/notice"


def _flat_id(bzp_number: str) -> str:
    """`2026/BZP 00240644` → `2026-BZP-00240644` (filesystem-safe)."""
    return bzp_number.replace("/", "-").replace(" ", "-")


def _api_url(*, page_size: int, notice_type: str, date_from: date, date_to: date) -> str:
    params = {
        "PageSize": page_size,
        "NoticeType": notice_type,
        "PublicationDateFrom": date_from.isoformat(),
        "PublicationDateTo": date_to.isoformat(),
    }
    return str(httpx.URL(BZP_API).copy_with(params=params))


def search(
    *,
    page_size: int = 50,
    notice_type: str = "ContractNotice",
    date_from: date | None = None,
    date_to: date | None = None,
    cpv_prefix: str | None = None,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    """Search the BZP API and return raw announcement dicts.

    `cpv_prefix` is applied client-side because the API doesn't expose
    a CPV filter (or at least none I could discover by probing). Pass
    `"72"` for all IT/computing services, `"72200000"` for software
    services specifically.
    """
    if date_from is None:
        date_from = date.today() - timedelta(days=7)
    if date_to is None:
        date_to = date.today()

    url = _api_url(
        page_size=page_size,
        notice_type=notice_type,
        date_from=date_from,
        date_to=date_to,
    )
    with httpx.Client(timeout=timeout) as client:
        response = client.get(url, headers={"Accept": "application/json"})
        response.raise_for_status()
        items: list[dict[str, Any]] = response.json()

    if cpv_prefix:
        items = [it for it in items if (it.get("cpvCode") or "").startswith(cpv_prefix)]
    return items


def fetch_full_record(
    bzp_number: str,
    *,
    sample_dir: Path,
    date_window_days: int = 30,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Pull one announcement by its `bzpNumber` and cache to `sample_dir`.

    The BZP API doesn't expose a `/notice/{id}` shape — only the search
    endpoint. So we search a wide window and filter client-side.
    Returns the raw dict; caller parses.

    Cached at `sample_dir/<flat-id>/raw.json` + `body.html`.
    """
    flat = _flat_id(bzp_number)
    target = sample_dir / flat
    raw_path = target / "raw.json"
    body_path = target / "body.html"

    if raw_path.exists():
        cached: dict[str, Any] = json.loads(raw_path.read_text(encoding="utf-8"))
        return cached

    target.mkdir(parents=True, exist_ok=True)

    # Best guess: published "recently" (operator passes a date_window_days
    # large enough to cover the gap). Caller may bump window if not found.
    items = search(
        page_size=200,
        date_from=date.today() - timedelta(days=date_window_days),
        date_to=date.today() + timedelta(days=1),
    )
    matches = [it for it in items if it.get("bzpNumber") == bzp_number]
    if not matches:
        raise LookupError(
            f"BZP {bzp_number!r} not found in last {date_window_days} days. "
            f"Try a larger window or check the bzpNumber typo."
        )
    record = matches[0]

    raw_path.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if record.get("htmlBody"):
        body_path.write_text(record["htmlBody"], encoding="utf-8")
    return record


def iter_recent_it(
    *,
    days: int = 7,
    limit: int = 20,
    cpv_prefix: str = "72",
) -> Iterable[dict[str, Any]]:
    """Yield up to `limit` IT-procurement announcements from the last `days`.

    Default `cpv_prefix="72"` covers all "Usługi informatyczne". Use
    `"72200000"` for software development specifically, `"48"` for
    "Pakiety oprogramowania i systemy informatyczne" (purchased
    software / licenses).
    """
    items = search(
        page_size=200,
        date_from=date.today() - timedelta(days=days),
        date_to=date.today(),
        cpv_prefix=cpv_prefix,
    )
    yield from items[:limit]
