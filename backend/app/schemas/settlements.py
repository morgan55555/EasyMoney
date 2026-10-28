"""Settlement request/response schemas (AGENTS.md §2.4, §2.12).

The sender of a proposal is the authenticated caller — §2.4 lets the debtor
propose a transfer to a creditor, so the request only names the recipient.
Money is always a string in the API (§2.12).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.schemas.money import check_money


class SettlementCreate(BaseModel):
    """A settlement proposal: the caller (debtor) pays ``amount`` to the
    recipient (creditor). The amount may be a partial payment (≤ debt)."""

    to_user_id: int
    amount: Decimal = Field(gt=0)

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, v: Decimal) -> Decimal:
        return check_money(v)


class SettlementOut(BaseModel):
    id: int
    group_id: int
    from_user_id: int
    from_name: str
    to_user_id: int
    to_name: str
    from_avatar_url: str | None = None
    to_avatar_url: str | None = None
    amount: str
    # "pending" | "confirmed" | "rejected" | "cancelled" (terminal)
    state: str
    confirmed_at: dt.datetime | None
    created_at: dt.datetime
