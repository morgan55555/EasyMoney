"""Web Push (AGENTS.md §2.14, §5.8, req 35): VAPID key, subscriptions, fire path.

Three layers, each tested where it is honest:

* ``/api/push/vapid-public-key`` — a pure function of the settings; tested by
  calling the route function directly (no app, no DB, no engine).
* ``/subscribe`` / ``/unsubscribe`` — driven through the real app (the shared
  session-scoped ``api_client``, test DB) so auth (401), the 204s, the
  ``(user_id, endpoint)`` upsert, and the idempotent unsubscribe are exercised
  end to end.
* the post-commit fire path — exercised at the service level in the test's own
  event loop (``with_test_db``): a fake sender is plugged into the push service
  registry to record deliveries, the app's session factory is pointed at the
  scenario factory (so ``push_notification_row`` reads the test DB on this
  loop), and a real ``ConnectionManager`` is plugged into the WS hook to assert
  the bell still fires alongside the push.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.push import vapid_public_key
from app.core import security
from app.core.config import get_settings
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.models.user import User
from app.services import push as push_service
from app.ws import hook
from app.ws.registry import ConnectionManager
from tests import bus_util

# Must stay in sync with conftest.TEST_DATABASE_URL.
TEST_DATABASE_URL = "postgresql+asyncpg://easymoney:easymoney@localhost:5432/easymoney_test"


# --- settings + registry control -------------------------------------------


def _env(monkeypatch: pytest.MonkeyPatch, **kv: object) -> None:
    """Apply settings overrides via the environment, then drop the cached
    Settings so the next read rebuilds from the patched env."""
    for key, value in kv.items():
        monkeypatch.setenv(key, str(value))
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _clean():
    """Isolate the process-wide push registry + WS hook around every test.

    The session-scoped ``api_client`` runs the real app's lifespan once, which
    installs a live WS manager/bus (and, only with VAPID keys, a push sender)
    into the module globals. A later test module (the WS hardening suite)
    relies on that manager, so each test here saves the current globals,
    resets them to a clean slate for its own body, and **restores** the app's
    state on teardown — no sender, manager, bus, or in-flight task leaks
    across tests (or across their separate event loops)."""
    orig_sender = push_service.get_sender()
    orig_manager = hook.get_manager()
    orig_bus = hook.get_bus()
    push_service.set_sender(None)
    hook.set_manager(None)
    hook.set_bus(None)
    try:
        yield
    finally:
        push_service.set_sender(orig_sender)
        hook.set_manager(orig_manager)
        hook.set_bus(orig_bus)
        get_settings.cache_clear()


# --- fake sender -----------------------------------------------------------


class FakeSender:
    """Records each ``send``; optionally simulates a push-service failure.

    ``status`` 404/410 raises ``DeadSubscriptionError`` (the row must be
    pruned); any other status raises a plain error (logged + swallowed, the
    row stays).
    """

    def __init__(self, status: int | None = None) -> None:
        self.sent: list[tuple[str, str, str, bytes]] = []
        self.status = status

    async def send(self, endpoint: str, p256dh: str, auth: str, payload: bytes) -> None:
        self.sent.append((endpoint, p256dh, auth, payload))
        if self.status in (404, 410):
            raise push_service.DeadSubscriptionError()
        if self.status is not None:
            raise RuntimeError(f"push service returned HTTP {self.status}")


# --- DB helpers (no truncation, for asserting HTTP-side writes) -------------


def _query(scenario: Callable[[async_sessionmaker[AsyncSession]], Awaitable[Any]]) -> Any:
    """Run a read-only scenario against the test DB **without** truncating it
    (so it can observe rows the ``api_client`` just committed)."""

    async def _main() -> Any:
        engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
            return await scenario(factory)
        finally:
            await engine.dispose()

    return asyncio.run(_main())


async def _subs_for(
    factory: async_sessionmaker[AsyncSession], user_id: int, endpoint: str
) -> list[tuple[str, str]]:
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(PushSubscription).where(
                        PushSubscription.user_id == user_id,
                        PushSubscription.endpoint == endpoint,
                    )
                )
            )
            .scalars()
            .all()
        )
        return [(r.p256dh, r.auth) for r in rows]


def _mk_user_1(run_db) -> str:
    """Truncate the test DB, create user 1, return a valid access token."""

    async def _scenario(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as db:
            db.add(User(id=1, email="a@t.local", password_hash="x", display_name="A"))
            await db.commit()

    run_db(_scenario)
    return security.create_access_token(1)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- vapid public key (pure settings function) -----------------------------


class TestVapidPublicKey:
    async def test_404_when_push_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(monkeypatch, DEBUG="true", JWT_SECRET="a" * 64)
        monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
        monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
        with pytest.raises(HTTPException) as excinfo:
            await vapid_public_key()
        assert excinfo.value.status_code == 404

    async def test_200_with_key_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env(
            monkeypatch,
            DEBUG="true",
            JWT_SECRET="a" * 64,
            VAPID_PUBLIC_KEY="base64url-pub",
            VAPID_PRIVATE_KEY="base64url-priv",
        )
        response = await vapid_public_key()
        assert response.key == "base64url-pub"


# --- subscribe / unsubscribe (real app, test DB) ---------------------------


class TestSubscribeUnsubscribe:
    def test_subscribe_requires_auth(self, api_client) -> None:
        response = api_client.post(
            "/api/push/subscribe",
            json={"endpoint": "https://push.test/1", "keys": {"p256dh": "k", "auth": "a"}},
        )
        assert response.status_code == 401

    def test_unsubscribe_requires_auth(self, api_client) -> None:
        response = api_client.post(
            "/api/push/unsubscribe", json={"endpoint": "https://push.test/1"}
        )
        assert response.status_code == 401

    def test_subscribe_upserts_on_user_endpoint(self, run_db, api_client) -> None:
        token = _mk_user_1(run_db)
        endpoint = "https://push.test/1"

        first = api_client.post(
            "/api/push/subscribe",
            json={"endpoint": endpoint, "keys": {"p256dh": "k1", "auth": "a1"}},
            headers=_auth(token),
        )
        assert first.status_code == 204
        assert _query(lambda f: _subs_for(f, 1, endpoint)) == [("k1", "a1")]

        # Same endpoint again with new keys: one row, keys replaced (upsert).
        second = api_client.post(
            "/api/push/subscribe",
            json={"endpoint": endpoint, "keys": {"p256dh": "k2", "auth": "a2"}},
            headers=_auth(token),
        )
        assert second.status_code == 204
        assert _query(lambda f: _subs_for(f, 1, endpoint)) == [("k2", "a2")]

    def test_unsubscribe_is_idempotent(self, run_db, api_client) -> None:
        token = _mk_user_1(run_db)
        endpoint = "https://push.test/1"
        api_client.post(
            "/api/push/subscribe",
            json={"endpoint": endpoint, "keys": {"p256dh": "k", "auth": "a"}},
            headers=_auth(token),
        )

        first = api_client.post(
            "/api/push/unsubscribe", json={"endpoint": endpoint}, headers=_auth(token)
        )
        assert first.status_code == 204
        assert _query(lambda f: _subs_for(f, 1, endpoint)) == []

        # The row is already gone; a second call is still a clean 204.
        second = api_client.post(
            "/api/push/unsubscribe", json={"endpoint": endpoint}, headers=_auth(token)
        )
        assert second.status_code == 204
        assert _query(lambda f: _subs_for(f, 1, endpoint)) == []


# --- the post-commit fire path (service level, test loop) -------------------


def _fire(scenario_body: Callable[[async_sessionmaker[AsyncSession]], Awaitable[Any]]):
    """Run ``scenario_body`` against the test DB in the current loop, with the
    push service's own session factory pointed at the scenario factory, and
    return whatever the body returns. A safety ``drain`` runs after the body
    (the body usually drains itself first so it can then assert on the DB)."""

    async def scenario(factory: async_sessionmaker[AsyncSession]) -> Any:
        original = push_service.get_session_factory
        push_service.get_session_factory = lambda: factory
        try:
            return await scenario_body(factory)
        finally:
            await push_service.drain()
            push_service.get_session_factory = original

    return scenario


async def _seed(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int, Notification]:
    """Create a user (preferred_language 'ru'), a subscription, and a
    committed notification row for that user; return (group_id, user_id, row)."""
    async with factory() as db:
        user = User(
            id=1,
            email="a@t.local",
            password_hash="x",
            display_name="A",
            preferred_language="ru",
        )
        db.add(user)
        # Flush the user before staging rows that reference it by raw FK value:
        # the app factory runs with autoflush off and there is no relationship to
        # order the inserts, so the user row must exist first (else FK violation).
        await db.flush()
        db.add(PushSubscription(user_id=1, endpoint="https://push.test/1", p256dh="k", auth="a"))
        notification = Notification(user_id=1, type="expense.created", payload={"note": "hi"})
        db.add(notification)
        await db.commit()
        await db.refresh(notification)
        return 1, 1, notification


class TestFirePath:
    async def test_one_push_per_subscription_with_payload_and_ws(self) -> None:
        sender = FakeSender()
        push_service.set_sender(sender)
        manager = ConnectionManager()
        socket = bus_util.FakeSocket()
        manager.connect(socket, 1)
        hook.set_manager(manager)

        async def body(factory: async_sessionmaker[AsyncSession]) -> None:
            group_id, _, notification = await _seed(factory)
            await hook.emit_notifications(group_id, [notification])

        await bus_util.with_test_db(_fire(body))

        # Exactly one push, to the one subscription of the one user.
        assert len(sender.sent) == 1
        endpoint, p256dh, auth, payload = sender.sent[0]
        assert (endpoint, p256dh, auth) == ("https://push.test/1", "k", "a")
        data = json.loads(payload.decode("utf-8"))
        assert data["lang"] == "ru"  # the user's preferred_language at send time
        assert set(data["notification"]) == {"id", "type", "payload", "created_at"}
        assert data["notification"]["type"] == "expense.created"
        assert data["notification"]["payload"] == {"note": "hi"}
        assert isinstance(data["notification"]["created_at"], str)

        # The bell still fired in parallel (AGENTS.md §2.14: additive layer).
        bell = socket.of_type("notification.created", group_id=1)
        assert len(bell) == 1
        assert bell[0]["data"]["type"] == "expense.created"

    async def test_dead_subscription_is_pruned(self) -> None:
        sender = FakeSender(status=404)
        push_service.set_sender(sender)
        manager = ConnectionManager()
        socket = bus_util.FakeSocket()
        manager.connect(socket, 1)
        hook.set_manager(manager)

        async def body(factory: async_sessionmaker[AsyncSession]) -> list[tuple[str, str]]:
            group_id, _, notification = await _seed(factory)
            await hook.emit_notifications(group_id, [notification])
            await push_service.drain()  # let the push task (and its prune) finish
            return await _subs_for(factory, 1, "https://push.test/1")

        rows = await bus_util.with_test_db(_fire(body))

        # The push was attempted once, then the dead row was pruned.
        assert len(sender.sent) == 1
        assert rows == []

    async def test_other_errors_keep_the_subscription(self) -> None:
        sender = FakeSender(status=503)
        push_service.set_sender(sender)
        manager = ConnectionManager()
        socket = bus_util.FakeSocket()
        manager.connect(socket, 1)
        hook.set_manager(manager)

        async def body(factory: async_sessionmaker[AsyncSession]) -> list[tuple[str, str]]:
            group_id, _, notification = await _seed(factory)
            await hook.emit_notifications(group_id, [notification])
            await push_service.drain()
            return await _subs_for(factory, 1, "https://push.test/1")

        rows = await bus_util.with_test_db(_fire(body))

        # The push was attempted once; a transient error is swallowed and the
        # subscription is kept (only 404/410 prune).
        assert len(sender.sent) == 1
        assert rows == [("k", "a")]

    async def test_no_sender_sends_nothing_and_errors_nothing(self) -> None:
        # No sender plugged in (VAPID unset): the fire path is a no-op.
        assert push_service.get_sender() is None
        manager = ConnectionManager()
        socket = bus_util.FakeSocket()
        manager.connect(socket, 1)
        hook.set_manager(manager)

        async def body(factory: async_sessionmaker[AsyncSession]) -> list[tuple[str, str]]:
            group_id, _, notification = await _seed(factory)
            await hook.emit_notifications(group_id, [notification])
            await push_service.drain()
            return await _subs_for(factory, 1, "https://push.test/1")

        rows = await bus_util.with_test_db(_fire(body))

        # No push task left in flight (drained), the bell still fired.
        assert len(push_service._tasks) == 0
        assert len(socket.of_type("notification.created", group_id=1)) == 1
        # And the subscription is untouched.
        assert rows == [("k", "a")]
