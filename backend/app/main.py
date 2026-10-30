"""Application factory.

Wires the FastAPI app: CORS, lifespan (WS registry here, scheduler in B9),
the DomainError -> HTTP mapping, and the routers (REST + WS).
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

import sqlalchemy
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import admin as admin_api
from app.api import auth as auth_api
from app.api import balances as balances_api
from app.api import expenses as expenses_api
from app.api import feed as feed_api
from app.api import groups as groups_api
from app.api import invites as invites_api
from app.api import me as me_api
from app.api import notifications as notifications_api
from app.api import push as push_api
from app.api import settlements as settlements_api
from app.api import users as users_api
from app.core import redis_client
from app.core.config import get_settings
from app.core.errors import (
    Conflict,
    DomainError,
    Forbidden,
    NotFound,
    Unauthorized,
    ValidationFailed,
)
from app.db.session import dispose_engine
from app.services import push as push_service
from app.services import storage as storage_service
from app.workers import scheduler as scheduler_worker
from app.ws import bus as ws_bus
from app.ws import endpoint as ws_api
from app.ws import hook as ws_hook
from app.ws.registry import ConnectionManager


def _sanitize_json(value: object) -> object:
    """Make a value safe for ``allow_nan=False`` JSON rendering.

    Starlette's ``JSONResponse`` renders with the stock encoder and
    ``allow_nan=False``; three things break it and would turn a 422 into a 500
    (H-16): a raw ``NaN``/``Infinity`` float, a ``Decimal`` (echoed as the
    ``input`` of money-field validation errors), and the ``ValueError``
    instance a field validator raises (echoed in the error's ``ctx``). All
    become their string form; everything else passes through.
    """
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, BaseException):
        return str(value)
    if isinstance(value, dict):
        return {key: _sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_json(item) for item in value]
    return value


class _TokenRedactFilter(logging.Filter):
    """Redact ``token=...`` query params from the uvicorn access log (S-4).

    The WS handshake carries the access token in the query string
    (``WS /ws?token=...``); the raw log line would otherwise leak it.
    """

    _RE = re.compile(r"(token=)[^&\s\"]+")

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "token=" in message:
            record.msg = self._RE.sub(r"\1***", message)
            record.args = None
        return True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # B8: the in-process WebSocket registry; services reach it through the
    # post-commit hook (app.ws.hook). The cross-instance bus (app.ws.bus):
    # LocalBus without REDIS_URL (exactly the pre-Redis behaviour), RedisBus
    # with one (its consumer task applies events to this replica's registry).
    # B9: the APScheduler runs the hourly overdue-debt reminder scan
    # (app.workers.scheduler, §2.7), single-flight through the Redis lock.
    # Storage backend is made ready (S3: the bucket is created, idempotently).
    manager = ConnectionManager()
    ws_hook.set_manager(manager)
    redis = redis_client.get_redis()
    bus: ws_bus.EventBus = (
        ws_bus.LocalBus(manager) if redis is None else ws_bus.RedisBus(redis, manager)
    )
    await bus.start()
    ws_hook.set_bus(bus)
    await storage_service.get_store().ready()
    scheduler = scheduler_worker.start_scheduler()
    # Web Push (AGENTS.md §2.14): the real sender is plugged in only when both
    # VAPID keys are configured; without them the emit-path push tasks no-op
    # (nothing sent, nothing errors — today's behaviour).
    settings = get_settings()
    if settings.push_enabled:
        push_service.set_sender(
            push_service.WebPushSender(settings.vapid_private_key, settings.vapid_subject)
        )
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        # Stop the bus consumer before anything else, so no event is applied
        # while the process is going down.
        await bus.close()
        ws_hook.set_bus(None)
        ws_hook.set_manager(None)
        # Let in-flight Web Push sends finish before the pool goes away.
        await push_service.drain()
        await redis_client.dispose_redis()
        # Close the pooled DB connections on the live loop before the event
        # loop itself goes down (clean shutdown in production; the same path
        # keeps test clients from leaving the pool behind on a dead loop).
        await dispose_engine()


# OpenAPI tag metadata: one documented section per domain (the routers set
# these tag names; the descriptions give each section context in Swagger/ReDoc).
_OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": "meta",
        "description": "Service health check.",
    },
    {
        "name": "auth",
        "description": (
            "Account lifecycle: register, login, token refresh, logout, the "
            "public registration status, and email confirmation "
            "(registration + email-change tokens)."
        ),
    },
    {
        "name": "me",
        "description": (
            "The signed-in user's profile: display name, avatar, email change "
            "(with optional confirmation), preferred language."
        ),
    },
    {
        "name": "users",
        "description": "User data other members may read: avatar images.",
    },
    {
        "name": "groups",
        "description": (
            "Groups: create, list, members, leave, close/reopen. A group has "
            "exactly one currency, set at creation."
        ),
    },
    {
        "name": "feed",
        "description": (
            "The group's append-only activity feed (cursor pagination, newest "
            "first)."
        ),
    },
    {
        "name": "invites",
        "description": (
            "Invitations by link or email: create, list, revoke, and accept "
            "(joining the group)."
        ),
    },
    {
        "name": "expenses",
        "description": (
            "Expenses: create, edit, delete (soft), equal / share-weighted / "
            "exact splits (computed server-side, §2.1), and receipt upload."
        ),
    },
    {
        "name": "notifications",
        "description": "In-app notifications (the bell): list and mark read.",
    },
    {
        "name": "balances",
        "description": (
            "Balances computed on read (§2.2): per group (net balances, "
            "pairwise debts, suggested transfers ≤ n−1) and across all groups."
        ),
    },
    {
        "name": "settlements",
        "description": (
            "Settlements: propose a payment (pending), then the recipient "
            "confirms or rejects; the sender may cancel. Balances move only on "
            "confirmation."
        ),
    },
    {
        "name": "admin",
        "description": (
            "Admin panel: create/edit users without email confirmation and "
            "platform statistics. The last remaining admin cannot be demoted."
        ),
    },
    {
        "name": "push",
        "description": (
            "Web Push (AGENTS.md §2.14): the VAPID public key and the "
            "subscribe/unsubscribe pair behind the Settings toggle. OS-level "
            "notifications are delivered post-commit from the notification "
            "emit path — no endpoint triggers a push."
        ),
    },
]


def create_app() -> FastAPI:
    settings = get_settings()
    # The API docs are opt-in via EXPOSE_API_DOCS (default false, matching the
    # compose and .env.example defaults): hidden by default so a deployment
    # does not expose the API surface; set EXPOSE_API_DOCS=true to enable
    # /api/docs (README §Environment).
    expose_docs = settings.expose_api_docs
    app = FastAPI(
        title="EasyMoney",
        description=(
            "Group expense tracking with exact decimal money, server-side "
            "splits, on-read balances, and live WebSocket updates. Money is "
            "always serialized as a string with the currency's minor digits; "
            "no currency conversion anywhere. See AGENTS.md §2 for the exact "
            "semantics."
        ),
        version="1.10",
        openapi_tags=_OPENAPI_TAGS,
        lifespan=lifespan,
        docs_url="/api/docs" if expose_docs else None,
        redoc_url="/api/redoc" if expose_docs else None,
        openapi_url="/api/openapi.json" if expose_docs else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health", tags=["meta"])
    async def health() -> dict[str, str]:
        # Additive Redis probe: "off" = local mode (not configured),
        # "on" = reachable, "error" = configured but not answering right now.
        redis = redis_client.get_redis()
        if redis is None:
            return {"status": "ok", "redis": "off"}
        try:
            await redis.ping()
        except Exception:
            return {"status": "ok", "redis": "error"}
        return {"status": "ok", "redis": "on"}

    # Domain errors raised by services map to the AGENTS.md §3.1 status codes.
    _status_for: dict[type[DomainError], int] = {
        NotFound: 404,
        Conflict: 409,
        Forbidden: 403,
        Unauthorized: 401,
        ValidationFailed: 422,
    }

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        status = _status_for.get(type(exc), 400)
        detail = str(exc) or type(exc).__name__
        return JSONResponse(status_code=status, content={"detail": detail})

    # Out-of-range path ids (e.g. 20-digit BIGINTs) overflow asyncpg's int64
    # binding at the DB layer; asyncpg reports that as a DataError, which
    # SQLAlchemy surfaces as a plain DBAPIError. Map only that specific case
    # to "not found" (X-1) and re-raise anything else, so real database
    # failures still surface.
    @app.exception_handler(sqlalchemy.exc.DBAPIError)
    async def dbapi_error_handler(request: Request, exc: sqlalchemy.exc.DBAPIError) -> JSONResponse:
        if "out of int64 range" in str(getattr(exc, "orig", "")):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        raise exc

    # Re-render 422 bodies with non-finite inputs sanitized (H-16).
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": _sanitize_json(exc.errors())})

    # Keep WS access tokens out of the access log (S-4).
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _TokenRedactFilter) for f in access_logger.filters):
        access_logger.addFilter(_TokenRedactFilter())

    app.include_router(auth_api.router)
    app.include_router(me_api.router)
    app.include_router(users_api.router)
    app.include_router(groups_api.router)
    app.include_router(feed_api.router)
    app.include_router(invites_api.router)
    app.include_router(expenses_api.router)
    app.include_router(notifications_api.router)
    app.include_router(balances_api.router)
    app.include_router(settlements_api.router)
    app.include_router(admin_api.router)
    app.include_router(push_api.router)
    app.include_router(ws_api.router)  # WS /ws?token=...

    return app


app = create_app()
