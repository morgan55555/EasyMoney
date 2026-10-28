"""Receipt lifecycle (AGENTS.md §3.5): upload, replace, delete, guards.

Drives the real app through a TestClient pointed at the test DB with an
isolated temp ``UPLOAD_DIR``, so the file-on-disk behavior (store, replace,
unlink) is asserted for real, not just the database row.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import security
from app.core.config import get_settings
from app.main import create_app
from app.models.membership import Membership
from app.models.user import User
from app.services import groups as groups_service

# Must stay in sync with conftest.TEST_DATABASE_URL.
TEST_DATABASE_URL = "postgresql+asyncpg://easymoney:easymoney@localhost:5432/easymoney_test"

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-receipt-bytes"


@pytest.fixture()
def receipt_client() -> Iterator[tuple[TestClient, Path]]:
    """The real app with settings pointed at the test DB and a temp upload dir.

    Function-scoped on purpose: ``app.db.session`` keeps one engine for the
    whole process, and its pooled connections bind to the event loop of the
    TestClient that first uses them. A second session-scoped client (there is
    one in ``test_api_security``) would otherwise reuse connections from
    another loop ("attached to a different loop"). Each test therefore gets a
    fresh app, a fresh engine (see ``_reset_app_engine``), and a fresh, empty
    upload dir.
    """
    original_db = os.environ.get("DATABASE_URL")
    original_uploads = os.environ.get("UPLOAD_DIR")
    upload_dir = Path(tempfile.mkdtemp(prefix="easymoney-receipts-"))
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["UPLOAD_DIR"] = str(upload_dir)
    get_settings.cache_clear()
    _reset_app_engine()
    app = create_app()
    with TestClient(app) as client:
        yield client, upload_dir
    _reset_app_engine()
    if original_db is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = original_db
    if original_uploads is None:
        os.environ.pop("UPLOAD_DIR", None)
    else:
        os.environ["UPLOAD_DIR"] = original_uploads
    get_settings.cache_clear()
    shutil.rmtree(upload_dir, ignore_errors=True)


def _reset_app_engine() -> None:
    """Drop the process-wide app engine so the next client's loop rebinds it."""
    from app.db import session as session_mod

    asyncio.run(session_mod.dispose_engine())
    session_mod._engine = None
    session_mod._session_factory = None


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _setup(run_db, client: TestClient) -> tuple[str, str, int]:
    """Truncate, create users 1..3, group 1 (USD, owner 1), expense 1 (paid by 1).

    Users 1 and 2 are group members (2 also participates in the expense);
    user 3 is NOT a member, so the membership guard can be exercised.
    Returns (token1, token3, expense_id).
    """

    async def _scenario(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as db:
            db.add(User(id=1, email="a@test.local", password_hash="x", display_name="A"))
            db.add(User(id=2, email="b@test.local", password_hash="x", display_name="B"))
            db.add(User(id=3, email="c@test.local", password_hash="x", display_name="C"))
            await db.commit()
            user = await db.get(User, 1)
            assert user is not None
            group = await groups_service.create_group(db, user, "G1", "USD")
            db.add(Membership(group_id=group.id, user_id=2))
            await db.commit()

    run_db(_scenario)
    token1 = security.create_access_token(1)
    token3 = security.create_access_token(3)
    response = client.post(
        "/api/groups/1/expenses",
        json={
            "payer_id": 1,
            "description": "Dinner",
            "amount": "10.00",
            "split_method": "equal",
            "category": "food",
            "date": "2026-01-02",
            "comment": "",
            "participants": [{"user_id": 1}, {"user_id": 2}],
        },
        headers=_auth(token1),
    )
    assert response.status_code == 201, response.text
    return token1, token3, response.json()["id"]


class TestUpload:
    def test_upload_stores_file_and_row(self, run_db, receipt_client) -> None:
        client, upload_dir = receipt_client
        token1, _token2, expense_id = _setup(run_db, client)
        response = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("receipt.png", PNG, "image/png")},
            headers=_auth(token1),
        )
        assert response.status_code == 201, response.text
        attachment = response.json()
        assert attachment["content_type"] == "image/png"
        assert attachment["size"] == len(PNG)
        # The file is on disk under the configured upload dir.
        files = list(upload_dir.iterdir())
        assert len(files) == 1
        assert files[0].read_bytes() == PNG
        # The expense now carries the attachment.
        expense = client.get(f"/api/expenses/{expense_id}", headers=_auth(token1)).json()
        assert expense["attachment"]["id"] == attachment["id"]

    def test_upload_replaces_in_place(self, run_db, receipt_client) -> None:
        client, upload_dir = receipt_client
        token1, _token2, expense_id = _setup(run_db, client)
        first = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("old.png", b"old-bytes", "image/png")},
            headers=_auth(token1),
        )
        assert first.status_code == 201, first.text
        second = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("new.png", b"new-bytes", "image/png")},
            headers=_auth(token1),
        )
        assert second.status_code == 201, second.text
        # Same row (same id) — the replace is in place, not a new attachment.
        assert second.json()["id"] == first.json()["id"]
        # Old file gone, new file present with the new bytes.
        files = {path.name: path.read_bytes() for path in upload_dir.iterdir()}
        assert list(files.values()) == [b"new-bytes"]
        # Download returns the replacement bytes.
        download = client.get(f"/api/attachments/{second.json()['id']}", headers=_auth(token1))
        assert download.status_code == 200
        assert download.content == b"new-bytes"

    def test_bad_content_type_is_422_and_stores_nothing(self, run_db, receipt_client) -> None:
        client, upload_dir = receipt_client
        token1, _token2, expense_id = _setup(run_db, client)
        response = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            headers=_auth(token1),
        )
        assert response.status_code == 422
        assert list(upload_dir.iterdir()) == []

    def test_over_size_is_422(self, run_db, receipt_client) -> None:
        client, _upload_dir = receipt_client
        token1, _token2, expense_id = _setup(run_db, client)
        big = b"x" * (11 * 1024 * 1024)  # default MAX_UPLOAD_MB is 10
        response = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("big.png", big, "image/png")},
            headers=_auth(token1),
        )
        assert response.status_code == 422


class TestDelete:
    def test_delete_removes_row_and_file(self, run_db, receipt_client) -> None:
        client, upload_dir = receipt_client
        token1, _token2, expense_id = _setup(run_db, client)
        upload = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token1),
        )
        assert upload.status_code == 201, upload.text
        attachment_id = upload.json()["id"]
        assert list(upload_dir.iterdir()) != []
        response = client.delete(f"/api/expenses/{expense_id}/receipt", headers=_auth(token1))
        assert response.status_code == 204
        # The expense no longer carries an attachment and the file is gone.
        expense = client.get(f"/api/expenses/{expense_id}", headers=_auth(token1)).json()
        assert expense["attachment"] is None
        assert list(upload_dir.iterdir()) == []
        # The old attachment id no longer resolves.
        gone = client.get(f"/api/attachments/{attachment_id}", headers=_auth(token1))
        assert gone.status_code == 404

    def test_delete_without_receipt_is_404(self, run_db, receipt_client) -> None:
        client, _upload_dir = receipt_client
        token1, _token2, expense_id = _setup(run_db, client)
        response = client.delete(f"/api/expenses/{expense_id}/receipt", headers=_auth(token1))
        assert response.status_code == 404

    def test_delete_allowed_on_closed_group(self, run_db, receipt_client) -> None:
        """§2.9 blocks only the upload on a closed group; removing the existing
        receipt stays possible (it is not a new attachment)."""
        client, _upload_dir = receipt_client
        token1, _token2, expense_id = _setup(run_db, client)
        client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token1),
        )
        close = client.post("/api/groups/1/close", headers=_auth(token1))
        assert close.status_code == 200, close.text
        response = client.delete(f"/api/expenses/{expense_id}/receipt", headers=_auth(token1))
        assert response.status_code == 204


class TestGuards:
    def test_upload_to_closed_group_is_409(self, run_db, receipt_client) -> None:
        client, _upload_dir = receipt_client
        token1, _token2, expense_id = _setup(run_db, client)
        close = client.post("/api/groups/1/close", headers=_auth(token1))
        assert close.status_code == 200, close.text
        response = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token1),
        )
        assert response.status_code == 409

    def test_non_member_cannot_upload_or_delete(self, run_db, receipt_client) -> None:
        client, _upload_dir = receipt_client
        _token1, token2, expense_id = _setup(run_db, client)
        upload = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token2),
        )
        assert upload.status_code == 403
        delete = client.delete(f"/api/expenses/{expense_id}/receipt", headers=_auth(token2))
        assert delete.status_code == 403
