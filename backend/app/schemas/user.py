"""User profile schemas (AGENTS.md §2.11)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str
    created_at: datetime


class MePatchRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
