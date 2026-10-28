"""Settlement endpoints (AGENTS.md §2.4).

The proposal's sender is the authenticated caller. Group-scoped routes hang
under ``/api/groups/{group_id}``; the transitions resolve the settlement and
enforce group membership via the deps (404/403).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import build_settlement_out
from app.core.deps import get_current_user, get_db, get_group_member, get_settlement_member
from app.models.group import Group
from app.models.settlement import Settlement
from app.models.user import User
from app.schemas.settlements import SettlementCreate, SettlementOut
from app.services import settlements as settlements_service

router = APIRouter(prefix="/api", tags=["settlements"])


@router.get("/groups/{group_id}/settlements", response_model=list[SettlementOut])
async def list_settlements(
    group: Group = Depends(get_group_member),
    db: AsyncSession = Depends(get_db),
) -> list[SettlementOut]:
    rows = await settlements_service.list_settlements(db, group)
    return [build_settlement_out(s) for s in rows]


@router.post("/groups/{group_id}/settlements", response_model=SettlementOut, status_code=201)
async def create_settlement(
    body: SettlementCreate,
    group: Group = Depends(get_group_member),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SettlementOut:
    settlement = await settlements_service.create_settlement(db, group, user, body)
    return build_settlement_out(settlement)


@router.post("/settlements/{settlement_id}/confirm", response_model=SettlementOut)
async def confirm_settlement(
    settlement: Settlement = Depends(get_settlement_member),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SettlementOut:
    result = await settlements_service.confirm_settlement(db, settlement, user)
    return build_settlement_out(result)


@router.post("/settlements/{settlement_id}/reject", response_model=SettlementOut)
async def reject_settlement(
    settlement: Settlement = Depends(get_settlement_member),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SettlementOut:
    result = await settlements_service.reject_settlement(db, settlement, user)
    return build_settlement_out(result)


@router.post("/settlements/{settlement_id}/cancel", response_model=SettlementOut)
async def cancel_settlement(
    settlement: Settlement = Depends(get_settlement_member),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SettlementOut:
    result = await settlements_service.cancel_settlement(db, settlement, user)
    return build_settlement_out(result)
