"""User-facing endpoints: avatar stream (AGENTS.md §2.13)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.errors import NotFound
from app.core.security import get_current_user
from app.models.user import User
from app.services import avatar as avatar_service

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("/{user_id}/avatar")
async def get_user_avatar(
    user_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Stream the avatar image for any authenticated user.

    Returns 404 when the target user has no avatar.
    """
    target = await db.get(User, user_id)
    if target is None:
        raise NotFound("User not found")
    result = avatar_service.decode_avatar(target)
    if result is None:
        raise NotFound("No avatar set")
    raw, content_type = result
    return Response(content=raw, media_type=content_type)
