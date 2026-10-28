"""Account endpoints: register / login / refresh / logout, registration
status, and email confirmation (AGENTS.md §2.11, §2.13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.auth import (
    AuthStatusOut,
    EmailConfirmOut,
    EmailConfirmRequest,
    EmailRevertOut,
    EmailRevertRequest,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)
from app.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await auth_service.register(
        db, body.email, body.password, body.display_name, body.preferred_language
    )
    access, refresh = await auth_service.issue_tokens(db, user)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await auth_service.login(db, body.email, body.password)
    access, refresh = await auth_service.issue_tokens(db, user)
    return TokenResponse(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    access, refresh_jwt = await auth_service.refresh(db, body.refresh_token)
    return TokenResponse(access_token=access, refresh_token=refresh_jwt)


@router.post("/logout", status_code=204)
async def logout(body: LogoutRequest, db: AsyncSession = Depends(get_db)) -> None:
    await auth_service.logout(db, body.refresh_token)


@router.get("/status", response_model=AuthStatusOut)
async def auth_status(db: AsyncSession = Depends(get_db)) -> AuthStatusOut:
    """Public endpoint: whether the registration form should be shown."""
    open = await auth_service.get_registration_status(db)
    return AuthStatusOut(registration_open=open)


@router.post("/email/confirm", response_model=EmailConfirmOut)
async def email_confirm(
    body: EmailConfirmRequest, db: AsyncSession = Depends(get_db)
) -> EmailConfirmOut:
    """Apply a confirmation token (register or change)."""
    user = await auth_service.confirm_email_token(db, body.token)
    return EmailConfirmOut(email=user.email, email_verified=user.email_verified)


@router.post("/email/revert", response_model=EmailRevertOut)
async def email_revert(
    body: EmailRevertRequest, db: AsyncSession = Depends(get_db)
) -> EmailRevertOut:
    """Apply a "this wasn't me" revert token for an email change (§2.13)."""
    user = await auth_service.revert_email_change(db, body.token)
    return EmailRevertOut(email=user.email)


@router.post("/email/resend", status_code=204)
async def email_resend(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Resend the user's pending register confirmation."""
    await auth_service.resend_confirmation(db, user, "register")
