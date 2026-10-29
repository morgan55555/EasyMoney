"""Receipt lifecycle (AGENTS.md §3.5): upload, replace, delete, the
creator-or-owner guards (§2.5), the receipt dying with its expense (§2.5),
and the ``expense.receipt.updated`` push to every subscribed tab (§3.2).

Drives the real app through a TestClient pointed at the test DB with an
isolated temp ``UPLOAD_DIR``, so the file-on-disk behavior (store, replace,
unlink) is asserted for real, not just the database row.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import joinedload

from app.core import security
from app.core.config import get_settings
from app.main import create_app
from app.models.expense import Expense
from app.models.group import Group
from app.models.membership import Membership
from app.models.user import User
from app.schemas.expenses import ExpenseCreate, ParticipantIn
from app.services import expenses as expenses_service
from app.services import groups as groups_service
from app.services import storage as storage_service
from app.ws import hook as ws_hook
from app.ws.bus import LocalBus
from app.ws.registry import ConnectionManager

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
    storage_service.reset_store()
    _reset_app_engine()
    app = create_app()
    with TestClient(app) as client:
        yield client, upload_dir
    _reset_app_engine()
    storage_service.reset_store()
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


def _setup(run_db, client: TestClient) -> tuple[str, str, str, int]:
    """Truncate, create users 1..3, group 1 (USD, owner 1), expense 1 (paid by 1).

    Users 1 and 2 are group members (2 also participates in the expense);
    user 3 is NOT a member, so the membership guard can be exercised.
    User 1 is the group owner and the expense creator; user 2 is a plain
    member, so the creator-or-owner rule (§2.5/§3.5) can be exercised.
    Returns (token1, token2, token3, expense_id).
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
    token2 = security.create_access_token(2)
    return token1, token2, token3, response.json()["id"]


class TestUpload:
    def test_upload_stores_file_and_row(self, run_db, receipt_client) -> None:
        client, upload_dir = receipt_client
        token1, _token2, _token3, expense_id = _setup(run_db, client)
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
        token1, _token2, _token3, expense_id = _setup(run_db, client)
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
        token1, _token2, _token3, expense_id = _setup(run_db, client)
        response = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            headers=_auth(token1),
        )
        assert response.status_code == 422
        assert list(upload_dir.iterdir()) == []

    def test_over_size_is_422(self, run_db, receipt_client) -> None:
        client, _upload_dir = receipt_client
        token1, _token2, _token3, expense_id = _setup(run_db, client)
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
        token1, _token2, _token3, expense_id = _setup(run_db, client)
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
        token1, _token2, _token3, expense_id = _setup(run_db, client)
        response = client.delete(f"/api/expenses/{expense_id}/receipt", headers=_auth(token1))
        assert response.status_code == 404

    def test_delete_allowed_on_closed_group(self, run_db, receipt_client) -> None:
        """§2.9 blocks only the upload on a closed group; removing the existing
        receipt stays possible (it is not a new attachment)."""
        client, _upload_dir = receipt_client
        token1, _token2, _token3, expense_id = _setup(run_db, client)
        client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token1),
        )
        close = client.post("/api/groups/1/close", headers=_auth(token1))
        assert close.status_code == 200, close.text
        response = client.delete(f"/api/expenses/{expense_id}/receipt", headers=_auth(token1))
        assert response.status_code == 204


class TestDeleteExpense:
    def test_deleting_the_expense_removes_its_receipt(self, run_db, receipt_client) -> None:
        """§2.5: the receipt dies with the expense — row and file both go."""
        client, upload_dir = receipt_client
        token1, _token2, _token3, expense_id = _setup(run_db, client)
        upload = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token1),
        )
        assert upload.status_code == 201, upload.text
        attachment_id = upload.json()["id"]
        assert list(upload_dir.iterdir()) != []
        response = client.delete(f"/api/expenses/{expense_id}", headers=_auth(token1))
        assert response.status_code == 204
        # The row is gone: the (still readable, soft-deleted) expense carries no
        # attachment and the old attachment id no longer resolves.
        expense = client.get(f"/api/expenses/{expense_id}", headers=_auth(token1)).json()
        assert expense["attachment"] is None
        assert list(upload_dir.iterdir()) == []
        gone = client.get(f"/api/attachments/{attachment_id}", headers=_auth(token1))
        assert gone.status_code == 404

    def test_deleting_an_expense_without_a_receipt_is_plain(self, run_db, receipt_client) -> None:
        client, upload_dir = receipt_client
        token1, _token2, _token3, expense_id = _setup(run_db, client)
        response = client.delete(f"/api/expenses/{expense_id}", headers=_auth(token1))
        assert response.status_code == 204
        assert list(upload_dir.iterdir()) == []


class TestGuards:
    def test_upload_to_closed_group_is_409(self, run_db, receipt_client) -> None:
        client, _upload_dir = receipt_client
        token1, _token2, _token3, expense_id = _setup(run_db, client)
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
        _token1, _token2, token3, expense_id = _setup(run_db, client)
        upload = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token3),
        )
        assert upload.status_code == 403
        delete = client.delete(f"/api/expenses/{expense_id}/receipt", headers=_auth(token3))
        assert delete.status_code == 403

    def test_plain_member_cannot_upload_or_delete(self, run_db, receipt_client) -> None:
        """§3.5/§2.5: a member who is neither the creator nor the owner gets
        403 on both attach and removal, while the owner can do both."""
        client, _upload_dir = receipt_client
        token1, token2, _token3, expense_id = _setup(run_db, client)
        upload = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token2),
        )
        assert upload.status_code == 403
        delete = client.delete(f"/api/expenses/{expense_id}/receipt", headers=_auth(token2))
        assert delete.status_code == 403
        # The owner (also the creator here) is unaffected.
        ok = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token1),
        )
        assert ok.status_code == 201, ok.text
        removed = client.delete(f"/api/expenses/{expense_id}/receipt", headers=_auth(token1))
        assert removed.status_code == 204

    def test_owner_can_manage_a_members_expense_receipt(self, run_db, receipt_client) -> None:
        """§3.5/§2.5: the group owner manages the receipt on an expense a plain
        member created (and the member can manage their own)."""
        client, _upload_dir = receipt_client
        token1, token2, _token3, _expense_id = _setup(run_db, client)
        # User 2 (a plain member) creates an expense they paid for.
        created = client.post(
            "/api/groups/1/expenses",
            json={
                "payer_id": 2,
                "description": "Snacks",
                "amount": "5.00",
                "split_method": "equal",
                "category": "food",
                "date": "2026-01-03",
                "participants": [{"user_id": 2}],
            },
            headers=_auth(token2),
        )
        assert created.status_code == 201, created.text
        member_expense = created.json()["id"]
        # The creator (user 2) may attach their own receipt.
        ok = client.post(
            f"/api/expenses/{member_expense}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token2),
        )
        assert ok.status_code == 201, ok.text
        # The owner (user 1, not the creator) may remove it.
        removed = client.delete(f"/api/expenses/{member_expense}/receipt", headers=_auth(token1))
        assert removed.status_code == 204


# --- WebSocket (AGENTS.md §3.2) --------------------------------------------------


class FakeSocket:
    """Duck-types a Starlette WebSocket for the registry's send path."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


def _receipt_events(sock: FakeSocket) -> list[dict[str, Any]]:
    return [
        event
        for event in (json.loads(text) for text in sock.sent)
        if event["type"] == "expense.receipt.updated"
    ]


async def _load_expense(db: AsyncSession, expense_id: int) -> Expense:
    result = await db.execute(
        select(Expense)
        .options(joinedload(Expense.group), joinedload(Expense.attachment))
        .where(Expense.id == expense_id)
    )
    return result.scalars().unique().one()


class TestReceiptEvents:
    """Attach and removal push ``expense.receipt.updated`` to every subscribed
    tab (§3.2) — a receipt added while an expense is being created becomes
    visible to the other members without a reload."""

    def test_upload_and_delete_reach_every_member_tab(self, run_db, receipt_client) -> None:
        _client, _upload_dir = receipt_client

        async def _scenario(factory: async_sessionmaker[AsyncSession]) -> None:
            async with factory() as db:
                db.add(User(id=1, email="a@test.local", password_hash="x", display_name="A"))
                db.add(User(id=2, email="b@test.local", password_hash="x", display_name="B"))
                await db.commit()
                owner = await db.get(User, 1)
                assert owner is not None
                group = await groups_service.create_group(db, owner, "G1", "USD")
                db.add(Membership(group_id=group.id, user_id=2))
                await db.commit()
                group_id = group.id

            # Both members' tabs are connected and subscribed before the receipt
            # operations (the manager and the local bus over it are plugged in
            # here, not by the app — the same pairing the lifespan makes).
            manager = ConnectionManager()
            ws_hook.set_manager(manager)
            ws_hook.set_bus(LocalBus(manager))
            try:
                tab_a, tab_b = FakeSocket(), FakeSocket()
                manager.connect(tab_a, 1)
                manager.subscribe_group(tab_a, group_id)
                manager.connect(tab_b, 2)
                manager.subscribe_group(tab_b, group_id)

                async with factory() as db:
                    group = await db.get(Group, group_id)
                    owner = await db.get(User, 1)
                    assert group is not None and owner is not None
                    expense = await expenses_service.create_expense(
                        db,
                        group,
                        owner,
                        ExpenseCreate(
                            payer_id=1,
                            description="dinner",
                            amount=Decimal("10.00"),
                            split_method="equal",
                            date=date(2026, 1, 2),
                            participants=[
                                ParticipantIn(user_id=1),
                                ParticipantIn(user_id=2),
                            ],
                        ),
                    )
                    expense_id = expense.id

                async with factory() as db:
                    expense = await _load_expense(db, expense_id)
                    owner = await db.get(User, 1)
                    assert owner is not None
                    attachment = await expenses_service.upload_receipt(
                        db, expense, owner, "r.png", "image/png", PNG
                    )
                    attachment_id = attachment.id

                for sock in (tab_a, tab_b):
                    events = _receipt_events(sock)
                    assert len(events) == 1
                    data = events[0]["data"]
                    assert data["expense_id"] == expense_id
                    assert data["attachment"]["id"] == attachment_id
                    assert data["attachment"]["content_type"] == "image/png"
                    assert data["attachment"]["size"] == len(PNG)
                    # Every group event carries the fresh balance snapshot (§3.2).
                    assert data["group_balances"]["currency"] == "USD"

                async with factory() as db:
                    expense = await _load_expense(db, expense_id)
                    owner = await db.get(User, 1)
                    assert owner is not None
                    await expenses_service.delete_receipt(db, expense, owner)

                for sock in (tab_a, tab_b):
                    events = _receipt_events(sock)
                    assert len(events) == 2
                    assert events[1]["data"]["expense_id"] == expense_id
                    assert events[1]["data"]["attachment"] is None
            finally:
                ws_hook.set_manager(None)
                ws_hook.set_bus(None)

        run_db(_scenario)
