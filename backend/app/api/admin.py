"""Admin panel endpoints: user management + statistics (AGENTS.md §2.13).

All routes require an admin (``get_current_admin``). Admins create and edit
users directly (no email confirmation) and read platform statistics.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin, get_db
from app.models.user import User
from app.schemas.admin import (
    AdminStatsOut,
    AdminUserCreateRequest,
    AdminUserOut,
    AdminUserPatchRequest,
    CurrencyTotalOut,
)
from app.schemas.user import avatar_url
from app.services import admin as admin_service
from app.services.currency import format_money

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _user_out(user: User) -> AdminUserOut:
    return AdminUserOut(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        is_admin=user.is_admin,
        email_verified=user.email_verified,
        avatar_url=avatar_url(user.id, user.avatar_data),
        created_at=user.created_at,
    )


@router.post("/users", response_model=AdminUserOut, status_code=201)
async def create_user(
    body: AdminUserCreateRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserOut:
    """Create a user without the confirmation flow (409 on duplicate email)."""
    user = await admin_service.create_user(db, body.email, body.password, body.display_name)
    return _user_out(user)


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> list[AdminUserOut]:
    """List all users."""
    users = await admin_service.list_users(db)
    return [_user_out(u) for u in users]


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def patch_user(
    user_id: int,
    body: AdminUserPatchRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserOut:
    """Edit a user (name / email / password / admin bit), no confirmation.

    The last remaining admin cannot be demoted (409).
    """
    user = await admin_service.patch_user(
        db,
        user_id,
        display_name=body.display_name,
        email=body.email,
        password=body.password,
        is_admin=body.is_admin,
    )
    return _user_out(user)


@router.get("/stats", response_model=AdminStatsOut)
async def stats(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
) -> AdminStatsOut:
    """Platform-wide statistics (money as per-currency strings, §2.12)."""
    s = await admin_service.get_stats(db)
    return AdminStatsOut(
        users_total=s.users_total,
        users_admin=s.users_admin,
        users_unverified=s.users_unverified,
        groups_active=s.groups_active,
        groups_closed=s.groups_closed,
        expenses_total=s.expenses_total,
        expenses_by_currency=[
            CurrencyTotalOut(currency=cur, total=format_money(total, cur))
            for cur, _count, total in s.expenses_by_currency
        ],
        settlements_by_state=s.settlements_by_state,
        settlements_confirmed_by_currency=[
            CurrencyTotalOut(currency=cur, total=format_money(total, cur))
            for cur, total in s.settlements_confirmed_by_currency
        ],
    )
