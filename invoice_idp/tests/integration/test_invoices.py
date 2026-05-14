"""Integration tests for the invoice upload + list + detail endpoints.

Storage and the arq enqueue are monkeypatched out so the test runs
without real MinIO / Redis. The actual worker flow is covered by a
separate test (when those services are running locally).
"""

from __future__ import annotations

import io
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.api import invoices as invoices_module
from src.models.invoice_record import Invoice


MIN_PDF = b"%PDF-1.4\n%fake\n%%EOF\n"


@pytest.fixture(autouse=True)
def _stub_storage_and_queue(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Replace S3 client and arq enqueue with in-memory stand-ins."""
    puts: list[tuple[str, bytes]] = []
    enqueued: list[str] = []

    class _FakeStorage:
        def put(self, key: str, content: bytes, content_type: str = "application/pdf") -> None:
            puts.append((key, content))

    async def _fake_enqueue(invoice_id: UUID) -> None:
        enqueued.append(str(invoice_id))

    monkeypatch.setattr(invoices_module, "get_storage", lambda: _FakeStorage())
    monkeypatch.setattr(invoices_module, "_enqueue_extraction", _fake_enqueue)
    return {"puts": puts, "enqueued": enqueued}


async def _signup_verify_login(client: AsyncClient, db_session: AsyncSession, email: str) -> None:
    await client.post("/auth/signup", json={"email": email, "password": "supersecret123"})
    # mark verified directly
    from src.models.user import User
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user is not None
    user.email_verified = True
    user.email_verification_token = None
    await db_session.commit()
    await client.post("/auth/login", json={"email": email, "password": "supersecret123"})


@pytest.mark.asyncio
async def test_upload_creates_invoice_and_enqueues_job(
    client: AsyncClient, db_session: AsyncSession, _stub_storage_and_queue: dict[str, list[Any]],
) -> None:
    await _signup_verify_login(client, db_session, "uploader@example.com")

    resp = await client.post(
        "/api/v1/invoices",
        files={"pdf": ("test.pdf", io.BytesIO(MIN_PDF), "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["original_filename"] == "test.pdf"

    invoice = await db_session.scalar(select(Invoice).where(Invoice.id == UUID(body["id"])))
    assert invoice is not None
    assert invoice.status == "pending"
    assert invoice.pdf_size_bytes == len(MIN_PDF)
    assert invoice.canonical_data is None

    assert len(_stub_storage_and_queue["puts"]) == 1
    assert _stub_storage_and_queue["puts"][0][0] == invoice.pdf_object_key
    assert _stub_storage_and_queue["enqueued"] == [body["id"]]


@pytest.mark.asyncio
async def test_upload_rejects_non_pdf(client: AsyncClient, db_session: AsyncSession) -> None:
    await _signup_verify_login(client, db_session, "notpdf@example.com")
    resp = await client.post(
        "/api/v1/invoices",
        files={"pdf": ("malicious.txt", io.BytesIO(b"this is not a pdf"), "application/pdf")},
    )
    assert resp.status_code == 400
    assert "magic bytes" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_rejects_empty_file(client: AsyncClient, db_session: AsyncSession) -> None:
    await _signup_verify_login(client, db_session, "empty@example.com")
    resp = await client.post(
        "/api/v1/invoices",
        files={"pdf": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_upload_requires_auth(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/invoices",
        files={"pdf": ("test.pdf", io.BytesIO(MIN_PDF), "application/pdf")},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_upload_requires_verified_email(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    # Signup without verifying
    await client.post(
        "/auth/signup",
        json={"email": "unverified@example.com", "password": "supersecret123"},
    )
    await client.post(
        "/auth/login",
        json={"email": "unverified@example.com", "password": "supersecret123"},
    )
    resp = await client.post(
        "/api/v1/invoices",
        files={"pdf": ("test.pdf", io.BytesIO(MIN_PDF), "application/pdf")},
    )
    assert resp.status_code == 403
    assert "verification" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_upload_is_idempotent_on_sha(
    client: AsyncClient, db_session: AsyncSession, _stub_storage_and_queue: dict[str, list[Any]],
) -> None:
    await _signup_verify_login(client, db_session, "dedupe@example.com")

    first = await client.post(
        "/api/v1/invoices",
        files={"pdf": ("a.pdf", io.BytesIO(MIN_PDF), "application/pdf")},
    )
    second = await client.post(
        "/api/v1/invoices",
        files={"pdf": ("b.pdf", io.BytesIO(MIN_PDF), "application/pdf")},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    # Second upload skips storage write + enqueue
    assert len(_stub_storage_and_queue["puts"]) == 1
    assert len(_stub_storage_and_queue["enqueued"]) == 1


@pytest.mark.asyncio
async def test_list_and_get_invoice(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _signup_verify_login(client, db_session, "lister@example.com")
    upload = await client.post(
        "/api/v1/invoices",
        files={"pdf": ("x.pdf", io.BytesIO(MIN_PDF), "application/pdf")},
    )
    inv_id = upload.json()["id"]

    listed = await client.get("/api/v1/invoices")
    assert listed.status_code == 200
    items = listed.json()
    assert len(items) == 1
    assert items[0]["id"] == inv_id

    detail = await client.get(f"/api/v1/invoices/{inv_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == inv_id
    assert detail.json()["canonical_data"] is None  # not yet extracted


@pytest.mark.asyncio
async def test_get_invoice_404_for_other_org(
    client: AsyncClient, db_session: AsyncSession,
) -> None:
    await _signup_verify_login(client, db_session, "owner@example.com")
    upload = await client.post(
        "/api/v1/invoices",
        files={"pdf": ("o.pdf", io.BytesIO(MIN_PDF), "application/pdf")},
    )
    other_id = upload.json()["id"]

    # log out, sign up a different user
    csrf = await client.get("/auth/csrf")
    await client.post("/auth/logout", headers={"X-CSRF-Token": csrf.json()["csrf_token"]})
    await _signup_verify_login(client, db_session, "intruder@example.com")

    resp = await client.get(f"/api/v1/invoices/{other_id}")
    assert resp.status_code == 404
