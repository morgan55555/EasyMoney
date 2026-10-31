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
    # Require confirmation (sent to the current address) on password change.
    # Active only when ``smtp_enabled`` is also true.
    password_change_confirmation: bool = False
    # How long a confirmation link stays valid, in hours. Applied to the
    # register, email-change and password-change confirmation links.
    confirmation_token_hours: int = 24
    # How long the "this wasn't me" revert link on the *previous* address stays
    # valid, in days, after an email change is requested. Within this window the
    # change can be reverted (cancelled if still pending, or switched back if
    # the new address already confirmed it).
    email_change_revert_days: int = 2
    # When false, only the first user can register (while the users table is
    # empty); after that registration is closed (403) and the UI hides the
    # register form.
    registration_open: bool = True
    # Max raw size (MB) of a user avatar upload (image stored as base64).
    max_avatar_mb: int = 2
    frontend_url: str = "http://localhost:8081"

    # --- Debt reminders (AGENTS.md §2.7) ---
    # App-wide defaults; every group may override both from its Settings tab.
    debt_overdue_days_default: int = 7
    reminder_interval_days_default: int = 7
    # Hard floor for the reminder interval: a group's interval can be lowered
    # to this value at most, never below it (422 otherwise).
    reminder_interval_days_min: int = 7

    # --- Invites ---
    invite_expiry_days: int = 7

    # --- Uploads ---
    max_upload_mb: int = 10
    # Where receipt files live: "s3" (an S3-compatible object store; LocalStack
    # in the compose stack) or "local" (plain disk under ``upload_dir`` — the
    # pre-S3 behaviour, still the default for bare dev and the test suite).
    storage_backend: str = "local"
    # S3-compatible endpoint. Empty = the provider's default endpoint (AWS).
    # The compose stack points this at the LocalStack service.
    s3_endpoint_url: str = ""
    s3_bucket: str = "easymoney-receipts"
    s3_region: str = "us-east-1"
    # Empty = let boto3 resolve credentials on its own (AWS_* env vars, IAM /
    # instance roles). Set dummy values (``test``) for LocalStack.
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    # Only used when ``storage_backend`` is "local".
    upload_dir: str = "/data/uploads"

    # --- Redis (cross-instance real-time bus + caches; optional) ---
    # Empty = local mode: WS fan-out stays process-local, the caches are
    # skipped and the reminder scan runs in every process — exactly the
    # pre-Redis behaviour (bare dev and the whole test suite run this way).
    redis_url: str = ""
    # Balance-snapshot cache TTL (safety net). The exact invalidation happens
    # on every group mutation via the same post-commit path that pushes the
    # WS event, so the TTL only covers a missed emit.
    snapshot_cache_ttl_s: int = 30
    # WS-handshake group-list cache TTL (every tab + every ~15 min token-
    # expiry reconnect re-queries otherwise).
    user_groups_cache_ttl_s: int = 300
    # Single-flight lock TTL for the hourly reminder scan: below the 1 h
    # interval (a dead holder can never block the next run) and far above any
    # realistic scan duration.
    scheduler_lock_ttl_s: int = 3300

    # --- Web Push (AGENTS.md §2.14) ---
    # VAPID key pair for OS-level push notifications. base64url, no padding.
    # Both empty = Web Push disabled (the public-key endpoint 404s and the
    # frontend shows the "unsupported" hint); generate them with
    # ``backend/scripts/generate_vapid.py``.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    # Contact for the VAPID "From" header; shown to the user by some browsers
    # when they are asked to allow notifications.
    vapid_subject: str = "mailto:admin@easymoney.local"

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

    @property
    def password_change_confirmation_active(self) -> bool:
        """Password-change confirmation is enforced only when the SMTP master
        switch and the dedicated toggle are both on. When off, the change is
        applied immediately."""
        return self.smtp_enabled and self.password_change_confirmation

    @property
    def push_enabled(self) -> bool:
        """Web Push is live only with both VAPID keys configured (AGENTS.md §2.14)."""
        return bool(self.vapid_public_key and self.vapid_private_key)

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
