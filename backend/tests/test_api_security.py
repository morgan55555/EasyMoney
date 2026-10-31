"""API-layer security regressions (PLAN WS2): A-7, X-1, H-7/H-14, H-16, S-2.

These drive the real FastAPI app (``create_app``) through a TestClient with
its database pointed at the test DB, so the full path — request parsing,
auth, dependency resolution, domain mapping, and the custom error handlers —
is exercised. The client is session-scoped and used as a context manager so
the app engine's pooled connections stay on one event loop.
"""

from __future__ import annotations

from datetime import UTC, datetime

import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.websockets import WebSocketDisconnect

from app.core import security
from app.core.config import get_settings
from app.models.user import User
from app.services import groups as groups_service


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _craft_token(sub: str) -> str:
    """A signature-valid access token with a caller-chosen ``sub``."""
    settings = get_settings()
    payload = {"sub": sub, "type": "access", "exp": int(datetime.now(UTC).timestamp()) + 600}
    return jwt.encode(payload, settings.jwt_secret, algorithm=security.ALGORITHM)


def _craft_token_wrong_sig(sub: str) -> str:
    """Well-formed payload but signed with a secret the server does not know."""
    payload = {"sub": sub, "type": "access", "exp": int(datetime.now(UTC).timestamp()) + 600}
    return jwt.encode(payload, "wrong-secret-long-enough-for-hs256", algorithm=security.ALGORITHM)


def _craft_token_expired(sub: str) -> str:
    """Signature-valid but with an ``exp`` already in the past."""
    settings = get_settings()
    payload = {"sub": sub, "type": "access", "exp": int(datetime.now(UTC).timestamp()) - 3600}
    return jwt.encode(payload, settings.jwt_secret, algorithm=security.ALGORITHM)


def _craft_token_alg_none(sub: str) -> str:
    """Unsigned token with ``alg: none`` — must be rejected (algorithm pinned)."""
    payload = {"sub": sub, "type": "access", "exp": int(datetime.now(UTC).timestamp()) + 600}
    return jwt.encode(payload, key="", algorithm="none")


def _register_user_1(run_db) -> str:
    """Truncate the test DB, create user 1 with group 1, return an access token.

    The group exists because FastAPI resolves route dependencies (group
    lookup) before the request body — body-validation tests still need a
    resolvable group to reach the 422.
    """

    async def _scenario(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as db:
            db.add(User(id=1, email="a@test.local", password_hash="x", display_name="A"))
            await db.commit()
            user = await db.get(User, 1)
            assert user is not None
            await groups_service.create_group(db, user, "G1", "USD")
            await db.commit()

    run_db(_scenario)
    return security.create_access_token(1)


def _expense_body(amount: str) -> str:
    return (
        '{"payer_id":1,"description":"x","date":"2026-01-01",'
        f'"participants":[{{"user_id":1}}],"amount":{amount}}}'
    )


async def _noop(factory: async_sessionmaker[AsyncSession]) -> None:
    pass


class TestCraftedTokens:
    """A-7: hostile ``sub`` claims must be a clean 401, never a traceback."""

    def test_non_integer_sub_is_401(self, api_client) -> None:
        response = api_client.get("/api/me", headers=_auth(_craft_token("12abc")))
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"

    def test_out_of_range_sub_is_401(self, api_client) -> None:
        # Parses as a Python int but overflows BIGINT: must be rejected before
        # it ever reaches the database.
        response = api_client.get("/api/me", headers=_auth(_craft_token("100000000000000000000")))
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid token"

    def test_wrong_signature_is_401(self, api_client) -> None:
        # Correct payload shape, signed with an unknown secret.
        response = api_client.get("/api/me", headers=_auth(_craft_token_wrong_sig("1")))
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or expired token"

    def test_expired_token_is_401(self, api_client) -> None:
        response = api_client.get("/api/me", headers=_auth(_craft_token_expired("1")))
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or expired token"

    def test_alg_none_token_is_401(self, api_client) -> None:
        # The decode pins algorithms=["HS256"], so an unsigned ``alg: none``
        # token is an algorithm error, never an accepted credential.
        response = api_client.get("/api/me", headers=_auth(_craft_token_alg_none("1")))
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid or expired token"


class TestOutOfRangeIds:
    def test_20_digit_group_id_is_404(self, run_db, api_client) -> None:
        """X-1: a 20-digit path id overflows asyncpg's int64 binding; the app
        must answer a clean 404 instead of a 500."""
        token = _register_user_1(run_db)
        response = api_client.get("/api/groups/99999999999999999999", headers=_auth(token))
        assert response.status_code == 404
        assert response.json() == {"detail": "Not found"}


class TestHostileMoneyBodies:
    """H-7/H-14/H-16: hostile money payloads must be a clean, parseable 422."""

    def test_raw_json_nan_is_422_not_500(self, run_db, api_client) -> None:
        # A raw JSON NaN used to make the 422 body itself fail to render
        # (allow_nan=False) and turn the 422 into a 500.
        token = _register_user_1(run_db)
        response = api_client.post(
            "/api/groups/1/expenses",
            content=_expense_body("NaN"),
            headers={**_auth(token), "Content-Type": "application/json"},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]  # the body must be renderable JSON
        assert any("amount" in loc for err in detail for loc in map(str, err["loc"]))

    @pytest.mark.parametrize(
        "raw_amount",
        [
            '"-0.001"',  # sub-precision string (H-7)
            '"1e-7"',  # scientific-notation string (H-14)
            "1e-7",  # scientific-notation number (H-14)
            '"1.2345"',  # finer than any supported currency (3 digits)
        ],
    )
    def test_sub_precision_amounts_are_422(self, run_db, api_client, raw_amount: str) -> None:
        token = _register_user_1(run_db)
        response = api_client.post(
            "/api/groups/1/expenses",
            content=_expense_body(raw_amount),
            headers={**_auth(token), "Content-Type": "application/json"},
        )
        assert response.status_code == 422
        response.json()  # clean, parseable 422 body


class TestWebsocketHardening:
    def test_binary_frame_is_ignored_then_ping_answered(self, run_db, api_client) -> None:
        """S-2: a binary frame must not kill the socket; a text ping after it
        still gets a pong."""
        token = _register_user_1(run_db)
        with api_client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_bytes(b"\x00\x01\x02")
            ws.send_text("ping")
            assert ws.receive_text() == "pong"

    def test_missing_or_bad_token_is_a_clean_close(self, run_db, api_client) -> None:
        """S14: a handshake with no token, or one that fails to decode, is a
        clean 4401 close (never a traceback / crash). The socket is never
        registered, so no event can fan out to it."""
        run_db(_noop)
        for url in ("/ws", "/ws?token=garbage-not-a-jwt"):
            with api_client.websocket_connect(url) as ws:
                with pytest.raises(WebSocketDisconnect) as exc_info:
                    ws.receive_text()
                assert exc_info.value.code == 4401

    def test_malformed_json_is_ignored_then_ping_answered(self, run_db, api_client) -> None:
        """S14: a text frame that is not the literal ping (e.g. malformed
        JSON) is not parsed at all and must not kill the socket; a ping after
        it still gets a pong."""
        token = _register_user_1(run_db)
        with api_client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_text('{"type": "expense.created"')  # malformed JSON
            ws.send_text("not json at all {{{")
            ws.send_text("ping")
            assert ws.receive_text() == "pong"
