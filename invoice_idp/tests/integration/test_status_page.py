"""Integration coverage for the public /status page.

The page probes Postgres, Redis, and S3/MinIO with hard timeouts and
renders a Polish HTML summary. Tests stub each probe so the assertions
exercise both code paths (all-up + degraded) without depending on the
test environment having those services reachable.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.fixture
def _clear_status_cache() -> None:
    """The route memoises its response for 30s. Drop the cache before
    each test so probe stubs actually run."""
    from src.app.web import routes

    routes._STATUS_CACHE["ts"] = 0.0
    routes._STATUS_CACHE["data"] = None


@pytest.mark.asyncio
async def test_status_page_all_services_up(
    client: AsyncClient, _clear_status_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.web import routes

    async def _ok() -> dict[str, bool]:
        return {"ok": True}

    monkeypatch.setattr(routes, "_probe_db", _ok)
    monkeypatch.setattr(routes, "_probe_redis", _ok)
    monkeypatch.setattr(routes, "_probe_storage", _ok)

    resp = await client.get("/status")
    assert resp.status_code == 200
    body = resp.text
    assert "Wszystko działa" in body
    assert "Aplikacja" in body
    assert "Baza danych" in body
    assert "Kolejka zadań" in body
    assert "Magazyn plików" in body
    # Each up service has the "Działa" badge.
    assert body.count("Działa") >= 4
    assert "Problem" not in body


@pytest.mark.asyncio
async def test_status_page_db_down_shows_degraded_state(
    client: AsyncClient, _clear_status_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.app.web import routes

    async def _ok() -> dict[str, bool]:
        return {"ok": True}

    async def _db_down() -> dict[str, object]:
        return {"ok": False, "error": "ConnectionRefusedError"}

    monkeypatch.setattr(routes, "_probe_db", _db_down)
    monkeypatch.setattr(routes, "_probe_redis", _ok)
    monkeypatch.setattr(routes, "_probe_storage", _ok)

    resp = await client.get("/status")
    assert resp.status_code == 200
    body = resp.text
    # Headline switches to the failure copy.
    assert "Wystąpił problem" in body
    assert "Wszystko działa" not in body
    # The error class name surfaces as the tooltip on the failing row.
    assert "ConnectionRefusedError" in body
    # Other services still show as up.
    assert body.count("Działa") >= 3


@pytest.mark.asyncio
async def test_status_page_does_not_require_auth(
    client: AsyncClient, _clear_status_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public status page — must NOT redirect to /login. Anonymously
    fetching /status should yield 200 even when no session cookie is
    present."""
    from src.app.web import routes

    async def _ok() -> dict[str, bool]:
        return {"ok": True}

    monkeypatch.setattr(routes, "_probe_db", _ok)
    monkeypatch.setattr(routes, "_probe_redis", _ok)
    monkeypatch.setattr(routes, "_probe_storage", _ok)

    # follow_redirects=False would catch a stray 303 to /login.
    resp = await client.get("/status", follow_redirects=False)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_status_page_caches_within_ttl(
    client: AsyncClient, _clear_status_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Within the 30s TTL, repeated hits hit the cache rather than
    re-running the probes — guards against /status traffic hammering
    downstream services."""
    from src.app.web import routes

    call_count = {"db": 0, "redis": 0, "storage": 0}

    async def _counted_db() -> dict[str, bool]:
        call_count["db"] += 1
        return {"ok": True}

    async def _counted_redis() -> dict[str, bool]:
        call_count["redis"] += 1
        return {"ok": True}

    async def _counted_storage() -> dict[str, bool]:
        call_count["storage"] += 1
        return {"ok": True}

    monkeypatch.setattr(routes, "_probe_db", _counted_db)
    monkeypatch.setattr(routes, "_probe_redis", _counted_redis)
    monkeypatch.setattr(routes, "_probe_storage", _counted_storage)

    await client.get("/status")
    await client.get("/status")
    await client.get("/status")

    # First call populates the cache; the next two read from it.
    assert call_count == {"db": 1, "redis": 1, "storage": 1}


@pytest.mark.asyncio
async def test_status_page_redis_down_shows_degraded_state(
    client: AsyncClient, _clear_status_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors the db-down test for the Redis probe — guards the queue
    row's failure surface independently. The db-down test alone can't
    catch a regression that only mis-routes the redis result."""
    from src.app.web import routes

    async def _ok() -> dict[str, bool]:
        return {"ok": True}

    async def _redis_down() -> dict[str, object]:
        return {"ok": False, "error": "TimeoutError"}

    monkeypatch.setattr(routes, "_probe_db", _ok)
    monkeypatch.setattr(routes, "_probe_redis", _redis_down)
    monkeypatch.setattr(routes, "_probe_storage", _ok)

    resp = await client.get("/status")
    assert resp.status_code == 200
    body = resp.text
    assert "Wystąpił problem" in body
    assert "Wszystko działa" not in body
    assert "TimeoutError" in body
    # DB + storage + Aplikacja still up → at least 3 green badges.
    assert body.count("Działa") >= 3


@pytest.mark.asyncio
async def test_status_page_storage_down_shows_degraded_state(
    client: AsyncClient, _clear_status_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Storage-down completes the per-probe failure matrix."""
    from src.app.web import routes

    async def _ok() -> dict[str, bool]:
        return {"ok": True}

    async def _storage_down() -> dict[str, object]:
        return {"ok": False, "error": "EndpointConnectionError"}

    monkeypatch.setattr(routes, "_probe_db", _ok)
    monkeypatch.setattr(routes, "_probe_redis", _ok)
    monkeypatch.setattr(routes, "_probe_storage", _storage_down)

    resp = await client.get("/status")
    assert resp.status_code == 200
    body = resp.text
    assert "Wystąpił problem" in body
    assert "EndpointConnectionError" in body
    assert body.count("Działa") >= 3


@pytest.mark.asyncio
async def test_status_page_all_services_down_still_renders(
    client: AsyncClient, _clear_status_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full outage of all three downstreams: the page must still
    render 200 (Aplikacja row is tautologically up — if we can serve
    this response, the app is alive) and surface each error class
    name. Guards against a future cleanup that would short-circuit
    rendering when overall_ok=False."""
    from src.app.web import routes

    async def _db_down() -> dict[str, object]:
        return {"ok": False, "error": "ConnectionRefusedError"}

    async def _redis_down() -> dict[str, object]:
        return {"ok": False, "error": "TimeoutError"}

    async def _storage_down() -> dict[str, object]:
        return {"ok": False, "error": "EndpointConnectionError"}

    monkeypatch.setattr(routes, "_probe_db", _db_down)
    monkeypatch.setattr(routes, "_probe_redis", _redis_down)
    monkeypatch.setattr(routes, "_probe_storage", _storage_down)

    resp = await client.get("/status")
    assert resp.status_code == 200
    body = resp.text
    assert "Wystąpił problem" in body
    # Each downstream's error class name surfaces on its own row.
    assert "ConnectionRefusedError" in body
    assert "TimeoutError" in body
    assert "EndpointConnectionError" in body
    # Aplikacja row stays green even in full outage.
    assert "Działa" in body


@pytest.mark.asyncio
async def test_status_page_cache_expires_after_ttl(
    client: AsyncClient, _clear_status_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Complement to `test_status_page_caches_within_ttl`: once
    `_STATUS_CACHE_TTL_SECONDS` has elapsed, the next hit must re-run
    the probes. Without this, a stale-cache regression (e.g. someone
    accidentally treating the cache as permanent) could mask a real
    outage indefinitely.

    Implemented by stomping `_STATUS_CACHE["ts"]` backwards by the TTL
    + a buffer rather than sleeping for 30s in the test."""
    from src.app.web import routes

    call_count = {"db": 0, "redis": 0, "storage": 0}

    async def _counted_db() -> dict[str, bool]:
        call_count["db"] += 1
        return {"ok": True}

    async def _counted_redis() -> dict[str, bool]:
        call_count["redis"] += 1
        return {"ok": True}

    async def _counted_storage() -> dict[str, bool]:
        call_count["storage"] += 1
        return {"ok": True}

    monkeypatch.setattr(routes, "_probe_db", _counted_db)
    monkeypatch.setattr(routes, "_probe_redis", _counted_redis)
    monkeypatch.setattr(routes, "_probe_storage", _counted_storage)

    # First hit populates the cache.
    await client.get("/status")
    assert call_count == {"db": 1, "redis": 1, "storage": 1}

    # Backdate the cache timestamp so the TTL window has "elapsed".
    routes._STATUS_CACHE["ts"] -= (routes._STATUS_CACHE_TTL_SECONDS + 5)

    # Next hit should re-run the probes.
    await client.get("/status")
    assert call_count == {"db": 2, "redis": 2, "storage": 2}
