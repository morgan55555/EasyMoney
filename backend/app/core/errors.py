"""Domain exceptions.

Services raise these; API routers map them to HTTP status codes (AGENTS.md
§3.1: 401 unauthenticated, 403 forbidden, 404 not found, 409 illegal state,
422 validation). Services never import FastAPI.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all domain errors."""


class NotFound(DomainError):
    """The referenced resource does not exist (HTTP 404)."""


class Conflict(DomainError):
    """The action conflicts with current state (HTTP 409)."""


class Forbidden(DomainError):
    """Authenticated but not allowed (HTTP 403)."""


class Unauthorized(DomainError):
    """Missing/invalid credentials (HTTP 401)."""


class ValidationFailed(DomainError):
    """A business-rule validation failed (HTTP 422)."""
