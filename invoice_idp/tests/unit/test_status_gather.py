"""Unit tests for `/status` aggregation logic.

Targets `src.app.web.routes._gather_status` — the pure-Python
coroutine that fans out the three probes and folds their results into
the dict the template renders. The route handler + template are
covered by `tests/integration/test_status_page.py`; this file is the
fast unit fence around the aggregation contract itself (overall_ok
folding, label mapping, concurrent dispatch, timestamp shape).

No DB / Redis / S3 needed — the three `_probe_*` helpers are
monkeypatched to async stubs returning canned dicts.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

from src.app.web import routes


def _ok_probe() -> Any:
    async def _impl() -> dict[str, bool]:
        return {"ok": True}

    return _impl


def _down_probe(error_name: str) -> Any:
    async def _impl() -> dict[str, Any]:
        return {"ok": False, "error": error_name}

    return _impl


@pytest.fixture(autouse=True)
def _clear_status_cache() -> None:
    """Drop the in-process /status cache before each test so cache
    state from a previous run can't leak in."""
    routes._STATUS_CACHE["ts"] = 0.0
    routes._STATUS_CACHE["data"] = None


@pytest.mark.asyncio
async def test_gather_status_all_up_overall_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "_probe_db", _ok_probe())
    monkeypatch.setattr(routes, "_probe_redis", _ok_probe())
    monkeypatch.setattr(routes, "_probe_storage", _ok_probe())

    data = await routes._gather_status()

    assert data["overall_ok"] is True
    assert set(data["checks"].keys()) == {
        "Aplikacja", "Baza danych", "Kolejka zadań", "Magazyn plików",
    }
    assert all(c["ok"] is True for c in data["checks"].values())


@pytest.mark.asyncio
async def test_gather_status_aplikacja_is_tautologically_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even if all three downstream probes fail, the `Aplikacja` row
    stays `ok: True` — if we're rendering the page at all, the app
    process is by definition up. Anything else would be a logic bug
    masquerading as a service outage."""
    monkeypatch.setattr(routes, "_probe_db", _down_probe("DBError"))
    monkeypatch.setattr(routes, "_probe_redis", _down_probe("RedisError"))
    monkeypatch.setattr(routes, "_probe_storage", _down_probe("S3Error"))

    data = await routes._gather_status()

    assert data["checks"]["Aplikacja"] == {"ok": True}


@pytest.mark.asyncio
async def test_gather_status_db_down_flips_overall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "_probe_db", _down_probe("ConnectionRefusedError"))
    monkeypatch.setattr(routes, "_probe_redis", _ok_probe())
    monkeypatch.setattr(routes, "_probe_storage", _ok_probe())

    data = await routes._gather_status()

    assert data["overall_ok"] is False
    assert data["checks"]["Baza danych"] == {
        "ok": False, "error": "ConnectionRefusedError",
    }
    assert data["checks"]["Kolejka zadań"]["ok"] is True
    assert data["checks"]["Magazyn plików"]["ok"] is True


@pytest.mark.asyncio
async def test_gather_status_redis_down_flips_overall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis-down case — integration test only covers db-down; this
    fences the same overall_ok=False propagation for the queue probe."""
    monkeypatch.setattr(routes, "_probe_db", _ok_probe())
    monkeypatch.setattr(routes, "_probe_redis", _down_probe("TimeoutError"))
    monkeypatch.setattr(routes, "_probe_storage", _ok_probe())

    data = await routes._gather_status()

    assert data["overall_ok"] is False
    assert data["checks"]["Kolejka zadań"] == {
        "ok": False, "error": "TimeoutError",
    }
    assert data["checks"]["Baza danych"]["ok"] is True
    assert data["checks"]["Magazyn plików"]["ok"] is True


@pytest.mark.asyncio
async def test_gather_status_storage_down_flips_overall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Storage-down case — also not covered at integration level."""
    monkeypatch.setattr(routes, "_probe_db", _ok_probe())
    monkeypatch.setattr(routes, "_probe_redis", _ok_probe())
    monkeypatch.setattr(routes, "_probe_storage", _down_probe("EndpointConnectionError"))

    data = await routes._gather_status()

    assert data["overall_ok"] is False
    assert data["checks"]["Magazyn plików"] == {
        "ok": False, "error": "EndpointConnectionError",
    }
    assert data["checks"]["Baza danych"]["ok"] is True
    assert data["checks"]["Kolejka zadań"]["ok"] is True


@pytest.mark.asyncio
async def test_gather_status_all_three_down_each_carries_own_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full-outage case: every downstream row carries its own error
    name (errors don't bleed across rows from concurrent gather)."""
    monkeypatch.setattr(routes, "_probe_db", _down_probe("ConnectionRefusedError"))
    monkeypatch.setattr(routes, "_probe_redis", _down_probe("TimeoutError"))
    monkeypatch.setattr(routes, "_probe_storage", _down_probe("EndpointConnectionError"))

    data = await routes._gather_status()

    assert data["overall_ok"] is False
    assert data["checks"]["Baza danych"]["error"] == "ConnectionRefusedError"
    assert data["checks"]["Kolejka zadań"]["error"] == "TimeoutError"
    assert data["checks"]["Magazyn plików"]["error"] == "EndpointConnectionError"


@pytest.mark.asyncio
async def test_gather_status_checked_at_is_iso_utc_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`checked_at` must be parseable as UTC ISO-8601 with second
    resolution. The template surfaces this string verbatim; a drift
    here would render garbage on the page."""
    monkeypatch.setattr(routes, "_probe_db", _ok_probe())
    monkeypatch.setattr(routes, "_probe_redis", _ok_probe())
    monkeypatch.setattr(routes, "_probe_storage", _ok_probe())

    data = await routes._gather_status()

    ts = data["checked_at"]
    assert isinstance(ts, str)
    # `datetime.isoformat(timespec="seconds")` with a tzaware UTC
    # value gives e.g. "2026-05-16T11:42:07+00:00". Pattern check is
    # enough — round-tripping through fromisoformat would also pass
    # but the regex catches drift to e.g. microsecond precision.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", ts), ts


@pytest.mark.asyncio
async def test_gather_status_runs_probes_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each probe sleeps 0.2s. If `_gather_status` were sequential
    the total would be >=0.6s; concurrent gather completes in ~0.2s.
    Guard against an accidental refactor to `await one; await two;
    await three` that would multiply real-world /status latency by
    the worst-case probe count."""
    delay = 0.2

    async def _slow_ok() -> dict[str, bool]:
        await asyncio.sleep(delay)
        return {"ok": True}

    monkeypatch.setattr(routes, "_probe_db", _slow_ok)
    monkeypatch.setattr(routes, "_probe_redis", _slow_ok)
    monkeypatch.setattr(routes, "_probe_storage", _slow_ok)

    loop = asyncio.get_event_loop()
    started = loop.time()
    await routes._gather_status()
    elapsed = loop.time() - started

    # Sequential lower bound would be ~3 * 0.2 = 0.6s. Allow a
    # generous concurrent ceiling of 0.45s to absorb scheduler jitter
    # on busy CI runners while still catching the sequential regression.
    assert elapsed < 0.45, f"probes look sequential: took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_gather_status_does_not_consult_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_gather_status` is the cache-miss path — it must always run
    the probes regardless of `_STATUS_CACHE` state. Caching is the
    route handler's responsibility, not the gatherer's. Locking this
    boundary down so future refactors don't accidentally short-circuit
    the gatherer when stale data sits in the cache."""
    call_count = {"n": 0}

    async def _counted_ok() -> dict[str, bool]:
        call_count["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(routes, "_probe_db", _counted_ok)
    monkeypatch.setattr(routes, "_probe_redis", _counted_ok)
    monkeypatch.setattr(routes, "_probe_storage", _counted_ok)

    # Pre-populate cache with a sentinel that would be obviously
    # wrong if leaked through.
    routes._STATUS_CACHE["ts"] = 999999.0
    routes._STATUS_CACHE["data"] = {"overall_ok": False, "checks": {}}

    await routes._gather_status()

    assert call_count["n"] == 3  # one per probe, every call
