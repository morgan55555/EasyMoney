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

from app.api import auth as auth_api
from app.api import balances as balances_api
from app.api import expenses as expenses_api
from app.api import feed as feed_api
from app.api import groups as groups_api
from app.api import invites as invites_api
from app.api import me as me_api
from app.api import notifications as notifications_api
from app.api import settlements as settlements_api
from app.core.config import get_settings
from app.core.errors import (
    Conflict,
    DomainError,
    Forbidden,
    NotFound,
    Unauthorized,
    ValidationFailed,
)
from app.workers import scheduler as scheduler_worker
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
    # post-commit hook (app.ws.hook). B9: the APScheduler runs the hourly
    # overdue-debt reminder scan (app.workers.scheduler, §2.7).
    ws_hook.set_manager(ConnectionManager())
    scheduler = scheduler_worker.start_scheduler()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        ws_hook.set_manager(None)


def create_app() -> FastAPI:
    settings = get_settings()
    # The API docs are opt-in via EXPOSE_API_DOCS: hidden by default so a
    # production deployment does not expose the API surface; enabled by
    # default in docker compose for local development (README §Environment).
    expose_docs = settings.expose_api_docs
    app = FastAPI(
        title="EasyMoney",
        version="0.1.0",
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
        return {"status": "ok"}

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
    app.include_router(groups_api.router)
    app.include_router(feed_api.router)
    app.include_router(invites_api.router)
    app.include_router(expenses_api.router)
    app.include_router(notifications_api.router)
    app.include_router(balances_api.router)
    app.include_router(settlements_api.router)
    app.include_router(ws_api.router)  # WS /ws?token=...

    return app


app = create_app()
