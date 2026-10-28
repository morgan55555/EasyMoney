"""Expense request/response schemas (AGENTS.md §2.5, §3.5).

Money is always a string in the API (§2.12). Request amounts arrive as
``Decimal`` (the JSON client sends strings/numbers); the service converts
them to minor units before touching the split engine.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from app.schemas.money import check_money, check_weight


class ParticipantIn(BaseModel):
    """One participant. ``weight`` for weighted splits, ``amount`` for exact."""

    user_id: int
    weight: Decimal | None = Field(default=None, gt=0)
    amount: Decimal | None = Field(default=None, ge=0)

    @field_validator("weight")
    @classmethod
    def _check_weight(cls, v: Decimal | None) -> Decimal | None:
        return check_weight(v) if v is not None else None

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, v: Decimal | None) -> Decimal | None:
        # ``ge=0`` already passed, so 0 is allowed for exact splits.
        return check_money(v) if v is not None else None


class ExpenseCreate(BaseModel):
    payer_id: int
    description: str = Field(min_length=1, max_length=200)
    amount: Decimal = Field(gt=0)
    split_method: str = Field(default="equal", pattern="^(equal|weighted|exact)$")
    category: str = Field(default="na", max_length=40)
    # ``dt.date`` (not a bare ``date`` alias): a field named ``date`` with a
    # default would shadow the type in the class namespace and break
    # Pydantic's deferred annotation evaluation.
    date: dt.date
    comment: str | None = Field(default=None, max_length=5000)
    participants: list[ParticipantIn] = Field(min_length=1)

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, v: Decimal) -> Decimal:
        return check_money(v)


class ExpenseUpdate(BaseModel):
    """Partial update; omitted fields keep their current value.

    ``comment`` is only cleared when the key is explicitly present as null.
    """

    payer_id: int | None = None
    description: str | None = Field(default=None, min_length=1, max_length=200)
    amount: Decimal | None = Field(default=None, gt=0)
    split_method: str | None = Field(default=None, pattern="^(equal|weighted|exact)$")
    category: str | None = Field(default=None, max_length=40)
    date: dt.date | None = None
    comment: str | None = Field(default=None, max_length=5000)
    participants: list[ParticipantIn] | None = None

    @field_validator("amount")
    @classmethod
    def _check_amount(cls, v: Decimal | None) -> Decimal | None:
        return check_money(v) if v is not None else None


class ParticipantOut(BaseModel):
    user_id: int
    display_name: str
    share: str
    weight: str | None = None


class AttachmentOut(BaseModel):
    id: int
    filename: str
    content_type: str
    size: int


class ExpenseOut(BaseModel):
    id: int
    group_id: int
    payer_id: int
    payer: str
    created_by: int
    description: str
    comment: str | None
    amount: str
    split_method: str
    category: str
    date: dt.date
    deleted_at: dt.datetime | None
    participants: list[ParticipantOut]
    attachment: AttachmentOut | None
    created_at: dt.datetime
