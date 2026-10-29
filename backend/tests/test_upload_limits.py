"""Upload limits & filename handling (TESTING_PLAN.md §7: S3, S4).

Drives the real app through a TestClient pointed at the test DB with an
isolated temp ``UPLOAD_DIR`` (the same pattern as ``test_receipts``), so the
file-on-disk guarantees are asserted for real:

  * S3 — hostile filenames (POSIX and Windows traversal) are neutralised:
    the stored name is always ``<uuid4-hex>.<ext>``, nothing escapes the
    upload dir;
  * S4 — the size caps are strict ``>`` (exactly at the cap is accepted, one
    byte over is 422 with nothing stored) and the content-type allow-list
    is enforced for both receipts and avatars.
"""

from __future__ import annotations

import asyncio
import os
import re
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
from app.models.user import User
from app.services import groups as groups_service
from app.services import storage as storage_service

# Must stay in sync with conftest.TEST_DATABASE_URL.
TEST_DATABASE_URL = "postgresql+asyncpg://easymoney:easymoney@localhost:5432/easymoney_test"

PNG = b"\x89PNG\r\n\x1a\n" + b"fake-receipt-bytes"

_UUID_NAME = re.compile(r"[0-9a-f]{32}\.(png|jpg|jpeg|gif|webp|pdf)$")


@pytest.fixture()
def up_client() -> Iterator[tuple[TestClient, Path]]:
    """The real app against the test DB with a temp upload dir and 1 MB caps.

    Function-scoped for the same engine-rebinding reason as
    ``test_receipts.receipt_client``.
    """
    original_db = os.environ.get("DATABASE_URL")
    original_uploads = os.environ.get("UPLOAD_DIR")
    original_max_upload = os.environ.get("MAX_UPLOAD_MB")
    original_max_avatar = os.environ.get("MAX_AVATAR_MB")
    upload_dir = Path(tempfile.mkdtemp(prefix="easymoney-uploads-"))
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["UPLOAD_DIR"] = str(upload_dir)
    os.environ["MAX_UPLOAD_MB"] = "1"
    os.environ["MAX_AVATAR_MB"] = "1"
    get_settings.cache_clear()
    storage_service.reset_store()
    _reset_app_engine()
    app = create_app()
    with TestClient(app) as client:
        yield client, upload_dir
    _reset_app_engine()
    storage_service.reset_store()
    for key, value in (
        ("DATABASE_URL", original_db),
        ("UPLOAD_DIR", original_uploads),
        ("MAX_UPLOAD_MB", original_max_upload),
        ("MAX_AVATAR_MB", original_max_avatar),
    ):
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
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


def _setup(run_db, client: TestClient) -> tuple[str, int]:
    """Truncate, create user 1 + group 1 (USD) + expense 1. Returns
    ``(access token, expense id)``."""

    async def _scenario(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as db:
            db.add(User(id=1, email="a@test.local", password_hash="x", display_name="A"))
            await db.commit()
            user = await db.get(User, 1)
            assert user is not None
            await groups_service.create_group(db, user, "G1", "USD")

    run_db(_scenario)
    token = security.create_access_token(1)
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
            "participants": [{"user_id": 1}],
        },
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return token, response.json()["id"]


# --- S3: hostile filenames ------------------------------------------------------


def test_s3_hostile_filenames_are_neutralised(run_db, up_client) -> None:
    client, upload_dir = up_client
    token, expense_id = _setup(run_db, client)
    # POSIX-style traversal in the multipart filename.
    response = client.post(
        f"/api/expenses/{expense_id}/receipt",
        files={"file": ("../../etc/passwd", PNG, "image/png")},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    name1 = response.json()["filename"]
    assert re.fullmatch(r"[0-9a-f]{32}\.png", name1), name1
    # Windows-style traversal.
    response = client.post(
        f"/api/expenses/{expense_id}/receipt",
        files={"file": ("..\\..\\windows\\system32\\evil.dll", PNG, "image/jpeg")},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    name2 = response.json()["filename"]
    assert re.fullmatch(r"[0-9a-f]{32}\.jpg", name2), name2
    # The second upload replaces the first (same expense): exactly the newest
    # uuid-named file remains on disk, nothing else.
    names = sorted(p.name for p in upload_dir.iterdir())
    assert names == [name2], names
    assert _UUID_NAME.fullmatch(name2), name2
    # The would-be escape targets do not exist.
    assert not (upload_dir / ".." / ".." / "etc" / "passwd").resolve().exists()
    assert not (
        upload_dir / ".." / ".." / "windows" / "system32" / "evil.dll"
    ).resolve().exists()


# --- S4: size caps and content-type allow-list -----------------------------------


def test_s4_receipt_size_cap_is_strict(run_db, up_client) -> None:
    client, upload_dir = up_client
    token, expense_id = _setup(run_db, client)
    # One byte over the 1 MiB cap: rejected, nothing stored.
    response = client.post(
        f"/api/expenses/{expense_id}/receipt",
        files={"file": ("big.png", b"x" * (1024 * 1024 + 1), "image/png")},
        headers=_auth(token),
    )
    assert response.status_code == 422, response.text
    assert list(upload_dir.iterdir()) == []
    # Exactly at the cap: accepted (the check is a strict '>').
    payload = PNG + b"x" * (1024 * 1024 - len(PNG))
    assert len(payload) == 1024 * 1024
    response = client.post(
        f"/api/expenses/{expense_id}/receipt",
        files={"file": ("exact.png", payload, "image/png")},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    # A disallowed content type: rejected, and the earlier file is untouched.
    response = client.post(
        f"/api/expenses/{expense_id}/receipt",
        files={"file": ("archive.zip", b"PK\x03\x04", "application/zip")},
        headers=_auth(token),
    )
    assert response.status_code == 422, response.text
    names = [p.name for p in upload_dir.iterdir()]
    assert len(names) == 1, names
    assert _UUID_NAME.fullmatch(names[0]), names[0]


def test_s4_avatar_size_cap_and_content_type(run_db, up_client) -> None:
    client, _ = up_client
    token, _expense_id = _setup(run_db, client)
    h = _auth(token)
    # One byte over the 1 MiB cap: rejected, nothing stored.
    response = client.post(
        "/api/me/avatar",
        files={"file": ("big.png", b"x" * (1024 * 1024 + 1), "image/png")},
        headers=h,
    )
    assert response.status_code == 422, response.text
    assert client.get("/api/users/1/avatar", headers=h).status_code == 404
    # A disallowed content type: rejected, nothing stored.
    response = client.post(
        "/api/me/avatar",
        files={"file": ("evil.html", b"<script>", "text/html")},
        headers=h,
    )
    assert response.status_code == 422, response.text
    assert client.get("/api/users/1/avatar", headers=h).status_code == 404
