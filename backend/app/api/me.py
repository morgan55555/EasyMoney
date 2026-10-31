"""Current-user profile endpoints: GET /me, PATCH /me, email change,
avatar set/clear (AGENTS.md §2.11, §2.13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.auth import (
    EmailChangeRequest,
    EmailChangeResponse,
    PasswordChangeRequest,
    PasswordChangeResponse,
)
from app.schemas.user import AvatarSetResponse, MePatchRequest, UserOut, avatar_url
from app.services import auth as auth_service
from app.services import avatar as avatar_service

router = APIRouter(prefix="/api", tags=["me"])


def _user_out(user: User) -> UserOut:
    settings = get_settings()
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
        is_admin=user.is_admin,
        email_verified=user.email_verified,
        preferred_language=user.preferred_language,
        preferred_currency=user.preferred_currency,
        avatar_url=avatar_url(user.id, user.avatar_data),
        email_change_confirmation=settings.email_change_confirmation_active,
        password_change_confirmation=settings.password_change_confirmation_active,
    )


@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)) -> UserOut:
    return _user_out(user)


@router.patch("/me", response_model=UserOut)
async def patch_me(
    body: MePatchRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    updated = await auth_service.update_profile(
        db, user, body.display_name, body.preferred_language, body.preferred_currency
    )
    return _user_out(updated)


@router.post("/me/email/change", response_model=EmailChangeResponse)
async def change_email(
    body: EmailChangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmailChangeResponse:
    status = await auth_service.change_email(db, user, body.new_email)
    return EmailChangeResponse(status=status)


@router.post("/me/password/change", response_model=PasswordChangeResponse)
async def change_password(
    body: PasswordChangeRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PasswordChangeResponse:
    status = await auth_service.change_password(db, user, body.new_password)
    return PasswordChangeResponse(status=status)


@router.post("/me/avatar", response_model=AvatarSetResponse)
async def set_avatar(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AvatarSetResponse:
    """Upload an avatar image (PNG, JPEG, WebP, GIF; ≤ MAX_AVATAR_MB)."""
    data = await file.read()
    content_type = file.content_type or ""
    await avatar_service.set_avatar(db, user, data, content_type)
    return AvatarSetResponse(avatar_url=f"/api/users/{user.id}/avatar")


@router.delete("/me/avatar", status_code=204)
async def clear_avatar(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Remove the current avatar."""
    await avatar_service.clear_avatar(db, user)
