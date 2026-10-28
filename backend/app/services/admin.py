"""Admin service: create/edit users without confirmation + statistics
(AGENTS.md §2.13).

Admins manage users directly (no email-confirmation tokens) and read
platform statistics. The last remaining admin can never be demoted
(lockout protection). Services raise domain errors; the API layer maps them
to HTTP statuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.security import hash_password
from app.models.expense import Expense
from app.models.group import Group
from app.models.settlement import Settlement
from app.models.user import User
from app.services.auth import _norm_email, _valid_email

# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------


async def create_user(
    db: AsyncSession,
    email: str,
    password: str,
    display_name: str | None,
) -> User:
    """Create a user directly (no confirmation flow, §2.13).

    The new user is ``email_verified=True`` (created by an admin, not by the
    self-registration flow) and is not an admin (admins promote explicitly).
    Raises 409 on a duplicate email.
    """
    email_norm = _norm_email(email)
    if not _valid_email(email_norm):
        raise ValidationFailed("A valid email address is required")
    if len(password) < 8:
        raise ValidationFailed("Password must be at least 8 characters")
    existing = await db.scalar(select(User).where(User.email == email_norm))
    if existing is not None:
        raise Conflict("An account with this email already exists")
    name = (display_name or "").strip() or email_norm.partition("@")[0]
    user = User(
        email=email_norm,
        password_hash=hash_password(password),
        display_name=name[:120],
        is_admin=False,
        email_verified=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def list_users(db: AsyncSession) -> list[User]:
    """All users, in id order (stable for the admin table)."""
    result = await db.execute(select(User).order_by(User.id))
    return list(result.scalars().all())


async def patch_user(
    db: AsyncSession,
    user_id: int,
    *,
    display_name: str | None = None,
    email: str | None = None,
    password: str | None = None,
    is_admin: bool | None = None,
) -> User:
    """Edit a user (no confirmation, §2.13). Omitted fields are unchanged.

    The **last remaining admin** cannot be demoted or have their admin bit
    removed — that would lock the platform out of admin access.
    """
    user = await db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")

    if display_name is not None:
        name = display_name.strip()
        if not name:
            raise ValidationFailed("Display name must not be empty")
        user.display_name = name[:120]

    if email is not None:
        email_norm = _norm_email(email)
        if not _valid_email(email_norm):
            raise ValidationFailed("A valid email address is required")
        if email_norm != user.email:
            existing = await db.scalar(
                select(User).where(User.email == email_norm, User.id != user.id)
            )
            if existing is not None:
                raise Conflict("An account with this email already exists")
            user.email = email_norm

    if password is not None:
        if len(password) < 8:
            raise ValidationFailed("Password must be at least 8 characters")
        user.password_hash = hash_password(password)

    if is_admin is not None and is_admin != user.is_admin:
        if not is_admin:
            # Demoting: block if this is the last remaining admin.
            admin_count = await db.scalar(
                select(func.count()).select_from(User).where(User.is_admin.is_(True))
            )
            if admin_count is not None and admin_count <= 1:
                raise Conflict("The last remaining admin cannot be demoted")
        user.is_admin = is_admin

    await db.commit()
    await db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdminStats:
    """Raw statistics (money as ``Decimal``); the API layer formats the strings."""

    users_total: int
    users_admin: int
    users_unverified: int
    groups_active: int
    groups_closed: int
    expenses_total: int
    # (currency, active-expense count, active-expense total)
    expenses_by_currency: tuple[tuple[str, int, Decimal], ...] = field(default_factory=tuple)
    # settlement state -> count
    settlements_by_state: dict[str, int] = field(default_factory=dict)
    # (currency, confirmed-settlement total)
    settlements_confirmed_by_currency: tuple[tuple[str, Decimal], ...] = field(
        default_factory=tuple
    )


async def get_stats(db: AsyncSession) -> AdminStats:
    """Compute platform-wide statistics (§2.13).

    Money aggregates follow the app's invariant: soft-deleted expenses are
    excluded, and only ``confirmed`` settlements count toward money totals.
    """
    users_total = await db.scalar(select(func.count()).select_from(User)) or 0
    users_admin = (
        await db.scalar(
            select(func.count()).select_from(User).where(User.is_admin.is_(True))
        )
        or 0
    )
    users_unverified = (
        await db.scalar(
            select(func.count()).select_from(User).where(User.email_verified.is_(False))
        )
        or 0
    )
    groups_active = (
        await db.scalar(
            select(func.count()).select_from(Group).where(Group.status == "active")
        )
        or 0
    )
    groups_closed = (
        await db.scalar(
            select(func.count()).select_from(Group).where(Group.status == "closed")
        )
        or 0
    )
    # Active (non-deleted) expenses only, per the sum-zero / aggregation rules.
    expenses_total = (
        await db.scalar(
            select(func.count()).select_from(Expense).where(Expense.deleted_at.is_(None))
        )
        or 0
    )

    expense_rows = (
        await db.execute(
            select(
                Group.currency, func.count(Expense.id), func.coalesce(func.sum(Expense.amount), 0)
            )
            .select_from(Expense)
            .join(Group, Expense.group_id == Group.id)
            .where(Expense.deleted_at.is_(None))
            .group_by(Group.currency)
            .order_by(Group.currency)
        )
    ).all()
    expenses_by_currency = tuple(
        (currency, int(count), Decimal(total))
        for currency, count, total in expense_rows
    )

    settlement_state_rows = (
        await db.execute(
            select(Settlement.state, func.count(Settlement.id)).group_by(Settlement.state)
        )
    ).all()
    settlements_by_state = {state: int(count) for state, count in settlement_state_rows}

    confirmed_rows = (
        await db.execute(
            select(Group.currency, func.coalesce(func.sum(Settlement.amount), 0))
            .select_from(Settlement)
            .join(Group, Settlement.group_id == Group.id)
            .where(Settlement.state == "confirmed")
            .group_by(Group.currency)
            .order_by(Group.currency)
        )
    ).all()
    settlements_confirmed_by_currency = tuple(
        (currency, Decimal(total)) for currency, total in confirmed_rows
    )

    return AdminStats(
        users_total=users_total,
        users_admin=users_admin,
        users_unverified=users_unverified,
        groups_active=groups_active,
        groups_closed=groups_closed,
        expenses_total=expenses_total,
        expenses_by_currency=expenses_by_currency,
        settlements_by_state=settlements_by_state,
        settlements_confirmed_by_currency=settlements_confirmed_by_currency,
    )
