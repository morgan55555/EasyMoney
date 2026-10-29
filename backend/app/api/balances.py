"""Balance endpoints (AGENTS.md §2.2, §2.3, §2.12).

``GET /groups/{id}/balances`` — per-member balances, the pairwise view and
the suggested transfers for one group (membership required). Served through
the snapshot cache (display reads only; fail-open, §3.2 multi-replica).
``GET /balances/all`` — the caller's balances across all groups, one row
per currency, no conversion (not cached: bounded and small).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db, get_group_member
from app.core.security import get_current_user
from app.models.group import Group
from app.models.user import User
from app.schemas.balances import (
    CurrencyBalanceOut,
    GroupBalancesOut,
    MemberBalanceOut,
    TransferOut,
)
from app.services import balances as balances_service
from app.services.currency import format_money

router = APIRouter(prefix="/api", tags=["balances"])


def _transfer_out(t: balances_service.Transfer, currency: str) -> TransferOut:
    return TransferOut(
        from_user_id=t.from_user_id,
        to_user_id=t.to_user_id,
        amount=format_money(t.amount, currency),
    )


@router.get("/groups/{group_id}/balances", response_model=GroupBalancesOut)
async def get_group_balances(
    group: Group = Depends(get_group_member),
    db: AsyncSession = Depends(get_db),
) -> GroupBalancesOut:
    # The wire dict (cache hit or fresh compute; money already formatted
    # strings) maps 1:1 onto the response schema.
    wire = await balances_service.load_balances_wire(db, group)
    return GroupBalancesOut(
        currency=wire["currency"],
        balances=[MemberBalanceOut(**row) for row in wire["balances"]],
        pairs=[TransferOut(**row) for row in wire["pairs"]],
        transfers=[TransferOut(**row) for row in wire["transfers"]],
    )


@router.get("/balances/all", response_model=list[CurrencyBalanceOut])
async def get_all_balances(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CurrencyBalanceOut]:
    by_currency = await balances_service.all_balances_for_user(db, user)
    return [
        CurrencyBalanceOut(currency=cur, balance=format_money(amt, cur))
        for cur, amt in sorted(by_currency.items())
    ]
