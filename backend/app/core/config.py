"""Application settings loaded from environment variables (pydantic-settings).

Boot-time ``JWT_SECRET`` validation (AGENTS.md §3.1, A-8):

* empty secret → auto-generate a random one when ``DEBUG``, fail otherwise;
* weak secret (shorter than 32 chars or a known dev default) → warn when
  ``DEBUG``, fail otherwise.

Production must set a strong secret explicitly.
"""

from __future__ import annotations

import logging
import secrets
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Secret values known to be development-only placeholders.
_WEAK_SECRETS = frozenset({"dev-only-change-me-in-prod", "change-me", "secret", "jwt_secret"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database ---
    postgres_user: str = "easymoney"
    postgres_password: str = "easymoney"
    postgres_db: str = "easymoney"
    postgres_host: str = "db"
    postgres_port: int = 5432
    database_url: str | None = None

    # --- Auth ---
    jwt_secret: str = ""
    jwt_access_ttl_min: int = 15
    jwt_refresh_ttl_days: int = 7

    # --- Debug & mail ---
    debug: bool = False
    # Mount the OpenAPI docs (Swagger at /api/docs, ReDoc at /api/redoc, the
    # spec at /api/openapi.json). Off by default — the app and docker compose
    # both default it to false — so deployments don't expose the full API
    # surface; set EXPOSE_API_DOCS=true to enable them (README §Environment).
    expose_api_docs: bool = False
    smtp_host: str | None = None
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = False
    # Master switch for outbound mail. When false, ``send_mail`` is a log-only
    # no-op (even with ``SMTP_HOST`` set) and every confirmation flow is
    # skipped (register + email change apply immediately).
    smtp_enabled: bool = True
    # Require email confirmation after registration. Active only when
    # ``smtp_enabled`` is also true.
    email_register_confirmation: bool = False
    # Require confirmation (sent to the previous address) on email change.
    # Active only when ``smtp_enabled`` is also true.
    email_change_confirmation: bool = False
    # When false, only the first user can register (while the users table is
    # empty); after that registration is closed (403) and the UI hides the
    # register form.
    registration_open: bool = True
    # Max raw size (MB) of a user avatar upload (image stored as base64).
    max_avatar_mb: int = 2
    frontend_url: str = "http://localhost:8081"

    # --- Debt reminders ---
    debt_overdue_days: int = 7
    reminder_interval_days: int = 7

    # --- Invites ---
    invite_expiry_days: int = 7

    # --- Uploads ---
    max_upload_mb: int = 10
    upload_dir: str = "/data/uploads"

    # --- CORS ---
    # Stored as a raw string so pydantic-settings does not attempt JSON parsing;
    # the list is derived via the property below (comma-separated in the env).
    cors_origins: str = "http://localhost:8081"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def register_confirmation_active(self) -> bool:
        """Register confirmation is enforced only when the SMTP master switch
        and the dedicated toggle are both on. When off, ``register`` marks the
        user verified immediately."""
        return self.smtp_enabled and self.email_register_confirmation

    @property
    def email_change_confirmation_active(self) -> bool:
        """Email-change confirmation is enforced only when the SMTP master
        switch and the dedicated toggle are both on. When off, the change is
        applied immediately."""
        return self.smtp_enabled and self.email_change_confirmation

    @model_validator(mode="after")
    def _check_jwt_secret(self) -> Settings:
        """Validate ``jwt_secret`` at boot (see module docstring)."""
        secret = self.jwt_secret
        if not secret:
            if self.debug:
                self.jwt_secret = secrets.token_hex(32)
                logger.warning(
                    "JWT_SECRET is not set; auto-generated a random secret "
                    "(DEBUG mode). Tokens will be invalidated on restart — "
                    "set JWT_SECRET for stable sessions."
                )
            else:
                raise ValueError("JWT_SECRET must be set (non-empty) when DEBUG is off")
            return self
        if len(secret) < 32 or secret in _WEAK_SECRETS:
            if self.debug:
                logger.warning(
                    "JWT_SECRET is weak (shorter than 32 chars or a known "
                    "development default). Set a strong secret for production."
                )
            else:
                raise ValueError(
                    "JWT_SECRET is too weak for production: use at least 32 "
                    "random characters and not a known development default"
                )
        return self

    @property
    def async_database_url(self) -> str:
        """Async SQLAlchemy URL (asyncpg driver).

        Honors an explicit DATABASE_URL override; otherwise composes it from the
        individual POSTGRES_* settings.
        """
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
