"""In-memory WebSocket connection registry (AGENTS.md §3.2).

One process-local registry answers "who is listening": which user owns each
socket and which groups each socket is subscribed to. Two fan-outs:

* ``broadcast_to_group`` — group events (expense.*, settlement.*, member.*,
  group.*): every socket subscribed to the group, i.e. all of its members;
* ``broadcast_to_users`` — user events (``notification.created``, the bell):
  only the sockets of the named users.

The whole server runs on one asyncio loop (one uvicorn worker), so the plain
dicts need no locks. A socket that fails a send is dropped on the spot, so
the next emit sees a consistent registry.
"""

from __future__ import annotations

from typing import Protocol


class Sendable(Protocol):
    """All the registry needs from a socket: send one text frame."""

    async def send_text(self, text: str) -> None: ...


class ConnectionManager:
    """Per-user socket registry with group- and user-scoped fan-outs."""

    def __init__(self) -> None:
        self._group_sockets: dict[int, set[Sendable]] = {}
        self._socket_users: dict[Sendable, int] = {}
        self._socket_groups: dict[Sendable, set[int]] = {}

    # --- lifecycle ----------------------------------------------------------

    def connect(self, ws: Sendable, user_id: int) -> None:
        """Register a socket as belonging to ``user_id`` (no groups yet)."""
        self._socket_users[ws] = user_id
        self._socket_groups.setdefault(ws, set())

    def disconnect(self, ws: Sendable) -> None:
        """Remove a socket from every group it is in (idempotent)."""
        self._socket_users.pop(ws, None)
        for group_id in self._socket_groups.pop(ws, set()):
            bucket = self._group_sockets.get(group_id)
            if bucket is not None:
                bucket.discard(ws)
                if not bucket:
                    del self._group_sockets[group_id]

    # --- subscription ---------------------------------------------------------

    def subscribe_group(self, ws: Sendable, group_id: int) -> None:
        """Subscribe one socket to one group (no-op for a dead socket)."""
        if ws not in self._socket_users:
            return
        self._socket_groups.setdefault(ws, set()).add(group_id)
        self._group_sockets.setdefault(group_id, set()).add(ws)

    def unsubscribe_group(self, ws: Sendable, group_id: int) -> None:
        """Unsubscribe one socket from one group (idempotent)."""
        groups = self._socket_groups.get(ws)
        if groups is not None:
            groups.discard(group_id)
        bucket = self._group_sockets.get(group_id)
        if bucket is not None:
            bucket.discard(ws)
            if not bucket:
                del self._group_sockets[group_id]

    def subscribe_user_to_group(self, user_id: int, group_id: int) -> None:
        """Dynamic re-subscribe (AGENTS.md §3.2): add the group to *every*
        socket of the user, so a mid-session join reaches every open tab."""
        for ws, uid in list(self._socket_users.items()):
            if uid == user_id:
                self.subscribe_group(ws, group_id)

    # --- fan-outs -------------------------------------------------------------

    def user_sockets(self, user_id: int) -> list[Sendable]:
        return [ws for ws, uid in self._socket_users.items() if uid == user_id]

    async def broadcast_to_group(self, group_id: int, message: str) -> None:
        """One JSON string to every socket subscribed to the group."""
        for ws in list(self._group_sockets.get(group_id, ())):
            await self._send(ws, message)

    async def broadcast_to_users(self, user_ids: set[int], message: str) -> None:
        """One JSON string to every socket of the named users."""
        for ws, uid in list(self._socket_users.items()):
            if uid in user_ids:
                await self._send(ws, message)

    async def _send(self, ws: Sendable, message: str) -> None:
        try:
            await ws.send_text(message)
        except Exception:
            # Dead socket (the client went away mid-send): drop it so the
            # next emit does not keep hitting it.
            self.disconnect(ws)
