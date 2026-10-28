"""Current-user profile endpoints: GET /me, PATCH /me, email change
(AGENTS.md §2.11, §2.13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.auth import EmailChangeRequest, EmailChangeResponse
from app.schemas.user import MePatchRequest, UserOut, avatar_url
from app.services import auth as auth_service

router = APIRouter(prefix="/api", tags=["me"])


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at,
        is_admin=user.is_admin,
        email_verified=user.email_verified,
        preferred_language=user.preferred_language,
        avatar_url=avatar_url(user.id, user.avatar_data),
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
        db, user, body.display_name, body.preferred_language
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
