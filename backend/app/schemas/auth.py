"""Request/response schemas for the account endpoints (AGENTS.md §2.11, §2.13).

Emails are plain ``str`` here; normalization (lowercase, trim) and shape
validation happen in the service so the same rules apply to every path that
creates a user (register, invite-accept, ...).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)
    # UI-language choice picked on the login page (§2.13); defaults to "en".
    preferred_language: str | None = Field(default=None, max_length=2)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AuthStatusOut(BaseModel):
    """Whether the registration form should be shown (§2.13)."""

    registration_open: bool


class EmailConfirmRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)


class EmailConfirmOut(BaseModel):
    """The result of applying a confirmation token (§2.13)."""

    ok: bool = True
    email: str
    email_verified: bool


class EmailRevertRequest(BaseModel):
    token: str = Field(min_length=1, max_length=128)


class EmailRevertOut(BaseModel):
    """The result of applying a "this wasn't me" revert token (§2.13)."""

    ok: bool = True
    email: str


class EmailChangeRequest(BaseModel):
    new_email: str = Field(min_length=3, max_length=320)


class EmailChangeResponse(BaseModel):
    """``"changed"`` (applied immediately) or ``"confirmation_sent"``."""

    status: str


class PasswordChangeRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=200)


class PasswordChangeResponse(BaseModel):
    """``"changed"`` (applied immediately) or ``"confirmation_sent"``."""

    status: str
