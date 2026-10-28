"""Group request/response schemas (AGENTS.md §2.9, §2.12).

Money is always a string in the API (§2.12): the balance chip arrives
pre-formatted with the group currency's minor digits.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    currency: str = Field(min_length=3, max_length=3)


class GroupOut(BaseModel):
    id: int
    name: str
    currency: str
    status: str
    created_by: int
    closed_at: datetime | None
    member_count: int
    balance: str


class GroupRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class MemberOut(BaseModel):
    id: int
    display_name: str
    email: str
    joined_at: datetime
    is_owner: bool
