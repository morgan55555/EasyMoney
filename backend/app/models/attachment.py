"""Receipt file attached to an expense (AGENTS.md §3.5).

One per expense (uploading replaces the previous row and file).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import BaseModel

if TYPE_CHECKING:
    from app.models.expense import Expense


class Attachment(BaseModel):
    __tablename__ = "attachments"

    expense_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("expenses.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # Stored on disk as ``<uuid>.<ext>`` under UPLOAD_DIR.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(80), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)

    expense: Mapped[Expense] = relationship(back_populates="attachment")
