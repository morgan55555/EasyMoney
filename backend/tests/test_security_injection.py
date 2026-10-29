"""Security & injection matrix (TESTING_PLAN.md §7: S1, S2, S5, S9–S11, S15).

Drives the real app through a TestClient pointed at the test DB (fresh
schema per test) with an isolated temp ``UPLOAD_DIR``, so hostile inputs are
checked against the live routers, not a mock:

  * S1  — SQL injection shapes in feed cursor / limit / group name (422 or
    round-trip as data, never 500);
  * S2  — path traversal in ``GET /api/attachments/{id}`` (404, no escape);
  * S5  — avatar content-type whitelist (422, nothing stored);
  * S9  — refresh-token rotation: old token revoked, logout revokes, a
    well-signed token with a never-issued jti is rejected (401);
  * S10 — CORS preflight: unknown origins get no ``Access-Control-Allow-Origin``;
  * S11 — non-member access control (403 on every group-scoped endpoint, 404
    for nonexistent groups, no owner powers for a plain member);
  * S15 — CRLF in a group name reaches the close-summary mail subject; the
    mail fails but the close itself must not be undone.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core import security
from app.core.config import get_settings
from app.main import create_app

# Must stay in sync with conftest.TEST_DATABASE_URL.
TEST_DATABASE_URL = "postgresql+asyncpg://easymoney:easymoney@localhost:5432/easymoney_test"

PASSWORD = "correct-horse-battery"


@pytest.fixture()
def sec_client() -> Iterator[tuple[TestClient, Path]]:
    """The real app against the test DB with a temp upload dir.

    Function-scoped for the same engine-rebinding reason as
    ``test_receipts.receipt_client``. Settings: 1 MB upload/avatar caps, one
    allowed CORS origin, SMTP enabled but host-less (log-only mail), and all
    email-confirmation flows off so registration is immediate.
    """
    original_db = os.environ.get("DATABASE_URL")
    original_uploads = os.environ.get("UPLOAD_DIR")
    original_max_upload = os.environ.get("MAX_UPLOAD_MB")
    original_max_avatar = os.environ.get("MAX_AVATAR_MB")
    original_cors = os.environ.get("CORS_ORIGINS")
    original_smtp_enabled = os.environ.get("SMTP_ENABLED")
    original_smtp_host = os.environ.get("SMTP_HOST")
    upload_dir = Path(tempfile.mkdtemp(prefix="easymoney-sec-"))
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    os.environ["UPLOAD_DIR"] = str(upload_dir)
    os.environ["MAX_UPLOAD_MB"] = "1"
    os.environ["MAX_AVATAR_MB"] = "1"
    os.environ["CORS_ORIGINS"] = "http://allowed.example"
    os.environ["SMTP_ENABLED"] = "true"
    os.environ["EMAIL_REGISTER_CONFIRMATION"] = "false"
    os.environ["EMAIL_CHANGE_CONFIRMATION"] = "false"
    os.environ["PASSWORD_CHANGE_CONFIRMATION"] = "false"
    os.environ.pop("SMTP_HOST", None)
    get_settings.cache_clear()
    _reset_app_engine()
    app = create_app()
    with TestClient(app) as client:
        yield client, upload_dir
    _reset_app_engine()
    for key, value in (
        ("DATABASE_URL", original_db),
        ("UPLOAD_DIR", original_uploads),
        ("MAX_UPLOAD_MB", original_max_upload),
        ("MAX_AVATAR_MB", original_max_avatar),
        ("CORS_ORIGINS", original_cors),
        ("SMTP_ENABLED", original_smtp_enabled),
        ("EMAIL_REGISTER_CONFIRMATION", None),
        ("EMAIL_CHANGE_CONFIRMATION", None),
        ("PASSWORD_CHANGE_CONFIRMATION", None),
        ("SMTP_HOST", original_smtp_host),
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


async def _noop(factory: async_sessionmaker[AsyncSession]) -> None:
    pass


def _register(client: TestClient, email: str, name: str) -> tuple[str, str]:
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": name},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], body["refresh_token"]


# --- S1: SQL injection shapes -------------------------------------------------


def test_s1_feed_cursor_and_names_are_inert(run_db, sec_client) -> None:
    client, _ = sec_client
    run_db(_noop)
    token, _ = _register(client, "alice@test.local", "Alice")
    h = _auth(token)
    response = client.post("/api/groups", json={"name": "G", "currency": "USD"}, headers=h)
    assert response.status_code == 201, response.text
    # A hostile cursor is an opaque-token error (422), never an SQL error.
    response = client.get(
        "/api/groups/1/activity", params={"cursor": "' OR 1=1--"}, headers=h
    )
    assert response.status_code == 422
    # A hostile limit is rejected by the int-typed parameter (422, never 500).
    response = client.get(
        "/api/groups/1/activity", params={"limit": "1; DROP TABLE users--"}, headers=h
    )
    assert response.status_code == 422
    # The tables are still there.
    response = client.get("/api/groups/1/activity", headers=h)
    assert response.status_code == 200
    # A SQLi-shaped group name round-trips as data.
    evil = "Robert'); DROP TABLE users;--"
    response = client.post("/api/groups", json={"name": evil, "currency": "USD"}, headers=h)
    assert response.status_code == 201, response.text
    assert response.json()["name"] == evil
    response = client.get("/api/groups", headers=h)
    assert response.status_code == 200
    assert evil in [g["name"] for g in response.json()]
    response = client.get("/api/groups/1/activity", headers=h)
    assert response.status_code == 200


# --- S2: path traversal --------------------------------------------------------


def test_s2_attachment_traversal_paths_are_404(run_db, sec_client) -> None:
    client, _ = sec_client
    run_db(_noop)
    token, _ = _register(client, "alice@test.local", "Alice")
    h = _auth(token)
    # The traversal path never matches the {attachment_id} route: 404 with or
    # without auth — no file read, no 500.
    assert client.get("/api/attachments/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/api/attachments/..%2F..%2Fetc%2Fpasswd", headers=h).status_code == 404
    # A well-formed id that exists for no one: 404 (no leak, no 500).
    assert client.get("/api/attachments/999999", headers=h).status_code == 404


# --- S5: avatar content-type whitelist -----------------------------------------


def test_s5_avatar_rejects_non_image_types(run_db, sec_client) -> None:
    client, _ = sec_client
    run_db(_noop)
    token, _ = _register(client, "alice@test.local", "Alice")
    h = _auth(token)
    response = client.post(
        "/api/me/avatar",
        files={"file": ("evil.html", b"<script>alert(1)</script>", "text/html")},
        headers=h,
    )
    assert response.status_code == 422, response.text
    response = client.post(
        "/api/me/avatar",
        files={"file": ("evil.exe", b"MZ", "application/x-executable")},
        headers=h,
    )
    assert response.status_code == 422, response.text
    # Nothing was stored.
    assert client.get("/api/users/1/avatar", headers=h).status_code == 404


# --- S9: refresh-token rotation -------------------------------------------------


def test_s9_refresh_rotation_and_revocation(run_db, sec_client) -> None:
    client, _ = sec_client
    run_db(_noop)
    _access, r1 = _register(client, "alice@test.local", "Alice")
    # Rotate: R1 is consumed, a fresh pair is issued.
    response = client.post("/api/auth/refresh", json={"refresh_token": r1})
    assert response.status_code == 200, response.text
    r2 = response.json()["refresh_token"]
    assert r2 != r1
    # Reusing the consumed token: 401.
    response = client.post("/api/auth/refresh", json={"refresh_token": r1})
    assert response.status_code == 401, response.text
    # Logout revokes R2; a later reuse is 401.
    response = client.post("/api/auth/logout", json={"refresh_token": r2})
    assert response.status_code == 204
    response = client.post("/api/auth/refresh", json={"refresh_token": r2})
    assert response.status_code == 401, response.text
    # A well-signed refresh JWT whose jti was never issued: 401.
    now = dt.datetime.now(dt.UTC)
    forged = jwt.encode(
        {
            "sub": "1",
            "type": "refresh",
            "jti": "forged-jti-never-issued",
            "iat": int(now.timestamp()),
            "exp": now + dt.timedelta(days=7),
        },
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    response = client.post("/api/auth/refresh", json={"refresh_token": forged})
    assert response.status_code == 401, response.text


# --- S10: CORS preflight ----------------------------------------------------------


def test_s10_preflight_grants_only_allowed_origins(run_db, sec_client) -> None:
    client, _ = sec_client
    run_db(_noop)
    response = client.options(
        "/api/groups",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in {k.lower() for k in response.headers}
    response = client.options(
        "/api/groups",
        headers={
            "Origin": "http://allowed.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://allowed.example"


# --- S11: non-member access control ---------------------------------------------


def _s11_setup(client: TestClient) -> tuple[dict, dict, dict, int, int]:
    """alice (owner) + bob (member) + carol (outsider) in group 1 (USD) with
    expense 1, a pending settlement and a receipt. Returns the three auth
    header dicts plus the settlement and attachment ids."""
    alice, _ = _register(client, "alice@test.local", "Alice")
    bob, _ = _register(client, "bob@test.local", "Bob")
    carol, _ = _register(client, "carol@test.local", "Carol")
    ha, hb, hc = _auth(alice), _auth(bob), _auth(carol)
    response = client.post("/api/groups", json={"name": "G", "currency": "USD"}, headers=ha)
    assert response.status_code == 201, response.text
    response = client.post("/api/groups/1/invites", json={"kind": "link"}, headers=ha)
    assert response.status_code == 201, response.text
    response = client.post("/api/invites/accept", json={"token": response.json()["token"]}, headers=hb)
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/groups/1/expenses",
        json={
            "payer_id": 1,
            "description": "Lunch",
            "amount": "10.00",
            "split_method": "equal",
            "category": "food",
            "date": "2026-01-01",
            "comment": "",
            "participants": [{"user_id": 1}, {"user_id": 2}],
        },
        headers=ha,
    )
    assert response.status_code == 201, response.text
    expense_id = response.json()["id"]
    response = client.post(
        "/api/groups/1/settlements", json={"to_user_id": 1, "amount": "1.00"}, headers=hb
    )
    assert response.status_code == 201, response.text
    settlement_id = response.json()["id"]
    response = client.post(
        f"/api/expenses/{expense_id}/receipt",
        files={"file": ("r.png", b"\x89PNG\r\n\x1a\nfake-bytes", "image/png")},
        headers=hb,
    )
    assert response.status_code == 201, response.text
    attachment_id = response.json()["id"]
    return ha, hb, hc, settlement_id, attachment_id


def test_s11_non_member_and_non_owner_access_control(run_db, sec_client) -> None:
    client, _ = sec_client
    run_db(_noop)
    ha, hb, hc, settlement_id, attachment_id = _s11_setup(client)
    # Carol is not a member: 403 on every group-scoped read and write.
    assert client.get("/api/groups/1/balances", headers=hc).status_code == 403
    assert client.get(f"/api/attachments/{attachment_id}", headers=hc).status_code == 403
    assert client.post(f"/api/settlements/{settlement_id}/confirm", headers=hc).status_code == 403
    assert client.post("/api/groups/1/close", headers=hc).status_code == 403
    assert client.patch("/api/expenses/1", json={"description": "x"}, headers=hc).status_code == 403
    # A nonexistent group is a 404 (no existence leak), not a 403.
    assert client.get("/api/groups/999/balances", headers=hc).status_code == 404
    # Bob is a member but not the owner: no close/reopen.
    assert client.post("/api/groups/1/close", headers=hb).status_code == 403
    assert client.post("/api/groups/1/reopen", headers=hb).status_code == 403
    # Sanity: the owner can still close.
    assert client.post("/api/groups/1/close", headers=ha).status_code == 200


# --- S15: CRLF in a group name ---------------------------------------------------


def test_s15_crlf_group_name_cannot_break_the_close(run_db, sec_client) -> None:
    client, _ = sec_client
    run_db(_noop)
    alice, _ = _register(client, "alice@test.local", "Alice")
    h = _auth(alice)
    evil = "Evil\r\nBcc: attacker@evil.example"
    response = client.post("/api/groups", json={"name": evil, "currency": "USD"}, headers=h)
    assert response.status_code == 201, response.text
    group_id = response.json()["id"]
    # Closing mails the final summary with the CRLF in the subject: the mail
    # construction fails, but the close itself must not be undone (§2.9).
    response = client.post(f"/api/groups/{group_id}/close", headers=h)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"
