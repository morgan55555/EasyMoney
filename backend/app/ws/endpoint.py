"""The WebSocket endpoint: ``WS /ws?token=<access token>`` (AGENTS.md §3.2).

Browsers cannot set WebSocket request headers, so the access token travels in
the query string. The token is validated once at handshake (access tokens are
stateless and never revoked, §2.11); the socket is dropped with close code
4401 when it expires mid-session, and the client reconnects with a fresh
token and refetches its state over REST before trusting it.

Client → server: pings only. A text frame ``ping`` is answered with
``pong``; every frame re-arms the expiry countdown. All mutations stay REST.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket
from jwt import PyJWTError
from sqlalchemy import select

from app.core import security
from app.db.session import get_session_factory
from app.models.membership import Membership
from app.models.user import User
from app.ws import hook as ws_hook
from app.ws import user_groups

router = APIRouter()


async def _reject(websocket: WebSocket, reason: str) -> None:
    """Close with an application code (4xxx) and a human-readable reason."""
    await websocket.close(code=4401, reason=reason)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    manager = ws_hook.get_manager()
    if manager is None:  # pragma: no cover - the lifespan always sets it
        await websocket.close(code=1011, reason="WebSocket subsystem not ready")
        return

    token = websocket.query_params.get("token", "")
    try:
        payload = security.decode_token(token)
    except PyJWTError:
        await _reject(websocket, "Invalid or expired token")
        return
    if payload.get("type") != "access" or "sub" not in payload:
        await _reject(websocket, "Not an access token")
        return
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        await _reject(websocket, "Invalid token subject")
        return

    factory = get_session_factory()
    async with factory() as db:
        user = await db.get(User, user_id)
        if user is None:
            await _reject(websocket, "Unknown user")
            return

    # The group set rides the handshake cache (AGENTS.md §3.2): every tab
    # connect / 15-min token-expiry reconnect would otherwise re-query
    # memberships. The DB query runs only on a cache miss (local mode, or a
    # Redis error — the cache is fail-open and transparent).
    async def _fetch_groups() -> list[int]:
        async with factory() as db:
            return list(
                (await db.execute(select(Membership.group_id).where(Membership.user_id == user_id)))
                .scalars()
                .all()
            )

    group_ids = await user_groups.user_group_ids(user_id, _fetch_groups)

    # Register the socket first — connect is the only path into _socket_users,
    # so subscribe_group below would otherwise silently no-op — then subscribe
    # it to every group the user is already a member of.
    manager.connect(websocket, user_id)
    for group_id in group_ids:
        manager.subscribe_group(websocket, group_id)

    exp = float(payload["exp"])
    try:
        while True:
            remaining = exp - datetime.now(UTC).timestamp()
            if remaining <= 0:
                await websocket.close(code=4401, reason="Token expired")
                break
            # ``receive()`` (not ``receive_text()``) so a binary frame is a
            # message to ignore, not an exception (S-2). The expiry race is
            # kept so an idle socket is dropped when its token lapses.
            receive_task = asyncio.create_task(websocket.receive())
            expire_task = asyncio.create_task(asyncio.sleep(remaining))
            done, _ = await asyncio.wait(
                {receive_task, expire_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if receive_task in done:
                expire_task.cancel()
                message = receive_task.result()
                if message.get("type") == "websocket.disconnect":
                    break
                text = message.get("text")
                if text is not None and text.strip() == "ping":
                    await websocket.send_text("pong")
                # binary frames and other text are ignored: no client commands
            else:
                receive_task.cancel()
                await websocket.close(code=4401, reason="Token expired")
                break
    finally:
        manager.disconnect(websocket)
