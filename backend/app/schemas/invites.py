"""Invitation request/response schemas (AGENTS.md §2.10)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class InviteCreate(BaseModel):
    kind: Literal["link", "email"]
    email_to: str | None = Field(default=None, max_length=320)


class InviteOut(BaseModel):
    id: int
    kind: str
    token: str
    url: str
    email_to: str | None
    created_by: int
    expires_at: datetime
    status: str
    used_by: int | None
    used_at: datetime | None


class InviteAccept(BaseModel):
    token: str = Field(min_length=8, max_length=64)
