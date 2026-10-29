"""S3 object storage for receipts (PLAN.md, AGENTS.md §3.5).

The same receipt lifecycle as ``test_receipts`` (upload, replace, delete,
the receipt dying with its expense, the 404 contract, the size/content-type
caps) driven against the **S3 backend**. The object store is mocked in-process
with ``moto`` (no LocalStack/Docker needed, hermetic and fast), and the
objects are asserted both through the API (the download endpoint) and
directly against the bucket with a separate boto3 client.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import boto3
import pytest
from botocore.client import BaseClient
from fastapi.testclient import TestClient
from moto import mock_aws
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
BUCKET = "easymoney-test-receipts"
REGION = "us-east-1"


@pytest.fixture()
def s3_client() -> Iterator[tuple[TestClient, BaseClient]]:
    """The real app against the test DB with the S3 backend enabled and moto
    mocking the object store.

    Function-scoped for the same engine-rebinding reason as
    ``test_receipts.receipt_client``. The mock must be active while the TestClient
    runs the app lifespan (``ready()`` creates the bucket), so it wraps the
    whole client lifetime.
    """
    saved: dict[str, str | None] = {}
    env = {
        "DATABASE_URL": TEST_DATABASE_URL,
        "STORAGE_BACKEND": "s3",
        # Empty endpoint: boto3's default (moto intercepts the calls in-process).
        "S3_ENDPOINT_URL": "",
        "S3_BUCKET": BUCKET,
        "S3_REGION": REGION,
        "S3_ACCESS_KEY_ID": "test",
        "S3_SECRET_ACCESS_KEY": "test",
        # boto3's own credential chain (the app client is built with empty
        # S3_* credentials in some configurations):
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_DEFAULT_REGION": REGION,
    }
    for key, value in env.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = value
    get_settings.cache_clear()
    storage_service.reset_store()
    _reset_app_engine()
    with mock_aws():
        app = create_app()
        with TestClient(app) as client:
            yield client, boto3.client("s3", region_name=REGION)
    _reset_app_engine()
    storage_service.reset_store()
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    get_settings.cache_clear()


def _reset_app_engine() -> None:
    """Drop the process-wide app engine so the next client's loop rebinds it."""
    from app.db import session as session_mod

    asyncio.run(session_mod.dispose_engine())
    session_mod._engine = None
    session_mod._session_factory = None


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _keys(sdk: BaseClient) -> list[str]:
    response = sdk.list_objects_v2(Bucket=BUCKET)
    return [item["Key"] for item in response.get("Contents", [])]


def _setup(run_db, client: TestClient) -> tuple[str, str, str, int]:
    """Truncate, create users 1..3, group 1 (USD, owner 1), expense 1 (paid by 1).

    User 3 is not a member (membership guards); user 1 is owner + creator,
    user 2 a plain member. Returns (token1, token2, token3, expense_id).
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
    def test_upload_stores_object_in_bucket(self, run_db, s3_client) -> None:
        client, sdk = s3_client
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
        # Exactly one object in the bucket, under the stored (uuid) key.
        keys = _keys(sdk)
        assert keys == [attachment["filename"]], keys
        # The object carries the exact bytes and the content type.
        body = sdk.get_object(Bucket=BUCKET, Key=attachment["filename"])
        assert body["Body"].read() == PNG
        assert body["ContentType"] == "image/png"
        # The expense now carries the attachment.
        expense = client.get(f"/api/expenses/{expense_id}", headers=_auth(token1)).json()
        assert expense["attachment"]["id"] == attachment["id"]

    def test_upload_replaces_in_place(self, run_db, s3_client) -> None:
        client, sdk = s3_client
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
        # The old object is gone; only the newest key holds the new bytes.
        keys = _keys(sdk)
        assert keys == [second.json()["filename"]], keys
        body = sdk.get_object(Bucket=BUCKET, Key=keys[0])
        assert body["Body"].read() == b"new-bytes"
        # Download returns the replacement bytes.
        download = client.get(f"/api/attachments/{second.json()['id']}", headers=_auth(token1))
        assert download.status_code == 200
        assert download.content == b"new-bytes"

    def test_over_size_is_422_and_stores_nothing(self, run_db, s3_client) -> None:
        client, sdk = s3_client
        token1, _token2, _token3, expense_id = _setup(run_db, client)
        big = b"x" * (11 * 1024 * 1024)  # default MAX_UPLOAD_MB is 10
        response = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("big.png", big, "image/png")},
            headers=_auth(token1),
        )
        assert response.status_code == 422
        assert _keys(sdk) == []

    def test_bad_content_type_is_422_and_stores_nothing(self, run_db, s3_client) -> None:
        client, sdk = s3_client
        token1, _token2, _token3, expense_id = _setup(run_db, client)
        response = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("notes.txt", b"hello", "text/plain")},
            headers=_auth(token1),
        )
        assert response.status_code == 422
        assert _keys(sdk) == []


class TestDelete:
    def test_delete_removes_object(self, run_db, s3_client) -> None:
        client, sdk = s3_client
        token1, _token2, _token3, expense_id = _setup(run_db, client)
        upload = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token1),
        )
        assert upload.status_code == 201, upload.text
        attachment_id = upload.json()["id"]
        assert _keys(sdk) != []
        response = client.delete(f"/api/expenses/{expense_id}/receipt", headers=_auth(token1))
        assert response.status_code == 204
        # The expense no longer carries an attachment and the object is gone.
        expense = client.get(f"/api/expenses/{expense_id}", headers=_auth(token1)).json()
        assert expense["attachment"] is None
        assert _keys(sdk) == []
        # The old attachment id no longer resolves.
        gone = client.get(f"/api/attachments/{attachment_id}", headers=_auth(token1))
        assert gone.status_code == 404

    def test_deleting_the_expense_removes_its_object(self, run_db, s3_client) -> None:
        """§2.5: the receipt dies with the expense — row and object both go."""
        client, sdk = s3_client
        token1, _token2, _token3, expense_id = _setup(run_db, client)
        upload = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token1),
        )
        assert upload.status_code == 201, upload.text
        attachment_id = upload.json()["id"]
        assert _keys(sdk) != []
        response = client.delete(f"/api/expenses/{expense_id}", headers=_auth(token1))
        assert response.status_code == 204
        expense = client.get(f"/api/expenses/{expense_id}", headers=_auth(token1)).json()
        assert expense["attachment"] is None
        assert _keys(sdk) == []
        gone = client.get(f"/api/attachments/{attachment_id}", headers=_auth(token1))
        assert gone.status_code == 404

    def test_missing_object_is_404(self, run_db, s3_client) -> None:
        """A row whose object is gone (e.g. bucket wiped) downloads as 404,
        the same contract as the old missing-file case."""
        client, sdk = s3_client
        token1, _token2, _token3, expense_id = _setup(run_db, client)
        upload = client.post(
            f"/api/expenses/{expense_id}/receipt",
            files={"file": ("r.png", PNG, "image/png")},
            headers=_auth(token1),
        )
        assert upload.status_code == 201, upload.text
        attachment = upload.json()
        # Simulate the object vanishing out from under the row.
        sdk.delete_object(Bucket=BUCKET, Key=attachment["filename"])
        response = client.get(f"/api/attachments/{attachment['id']}", headers=_auth(token1))
        assert response.status_code == 404


class TestLifecycle:
    def test_ready_creates_bucket_and_is_idempotent(self, s3_client) -> None:
        _client, sdk = s3_client
        # The app's lifespan already ran ready() (the bucket exists below);
        # calling it again must not raise (BucketAlreadyOwnedByYou is swallowed).
        asyncio.run(storage_service.get_store().ready())
        names = [bucket["Name"] for bucket in sdk.list_buckets()["Buckets"]]
        assert BUCKET in names

    def test_receipt_event_payload_on_s3(self, run_db, s3_client) -> None:
        """The ``expense.receipt.updated`` push carries the attachment metadata
        (id/filename/content_type/size) regardless of the backend."""
        _client, _sdk = s3_client

        class FakeSocket:
            def __init__(self) -> None:
                self.sent: list[str] = []

            async def send_text(self, text: str) -> None:
                self.sent.append(text)

        async def _scenario(factory: async_sessionmaker[AsyncSession]) -> None:
            async with factory() as db:
                db.add(User(id=1, email="a@test.local", password_hash="x", display_name="A"))
                await db.commit()
                owner = await db.get(User, 1)
                assert owner is not None
                group = await groups_service.create_group(db, owner, "G1", "USD")
                await db.commit()
                group_id = group.id

            manager = ConnectionManager()
            ws_hook.set_manager(manager)
            ws_hook.set_bus(LocalBus(manager))
            try:
                tab = FakeSocket()
                manager.connect(tab, 1)
                manager.subscribe_group(tab, group_id)

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
                            participants=[ParticipantIn(user_id=1)],
                        ),
                    )
                    expense_id = expense.id

                async with factory() as db:
                    result = await db.execute(
                        select(Expense)
                        .options(joinedload(Expense.group), joinedload(Expense.attachment))
                        .where(Expense.id == expense_id)
                    )
                    expense = result.scalars().unique().one()
                    owner = await db.get(User, 1)
                    assert owner is not None
                    attachment = await expenses_service.upload_receipt(
                        db, expense, owner, "r.png", "image/png", PNG
                    )

                events = [
                    event
                    for event in (json.loads(text) for text in tab.sent)
                    if event["type"] == "expense.receipt.updated"
                ]
                assert len(events) == 1
                data: dict[str, Any] = events[0]["data"]
                assert data["expense_id"] == expense_id
                assert data["attachment"]["id"] == attachment.id
                assert data["attachment"]["content_type"] == "image/png"
                assert data["attachment"]["size"] == len(PNG)
                assert data["group_balances"]["currency"] == "USD"
            finally:
                ws_hook.set_manager(None)
                ws_hook.set_bus(None)

        run_db(_scenario)
