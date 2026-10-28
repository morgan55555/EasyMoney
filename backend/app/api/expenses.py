"""Expense endpoints (AGENTS.md §2.1, §2.5, §3.5; README API overview).

Group-scoped routes hang under ``/api/groups/{group_id}``; the expense-scoped
ones resolve the expense and check membership via the deps. Receipt upload is
multipart with a single ``file`` field.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import build_attachment_out, build_expense_out
from app.core.deps import (
    get_attachment_member,
    get_current_user,
    get_db,
    get_expense_member,
    get_group_member,
)
from app.models.attachment import Attachment
from app.models.expense import Expense
from app.models.group import Group
from app.models.user import User
from app.schemas.expenses import AttachmentOut, ExpenseCreate, ExpenseOut, ExpenseUpdate
from app.services import expenses as expenses_service

router = APIRouter(prefix="/api", tags=["expenses"])


@router.get("/groups/{group_id}/expenses", response_model=list[ExpenseOut])
async def list_expenses(
    group: Group = Depends(get_group_member),
    db: AsyncSession = Depends(get_db),
) -> list[ExpenseOut]:
    expenses = await expenses_service.list_expenses(db, group)
    return [build_expense_out(e) for e in expenses]


@router.post("/groups/{group_id}/expenses", response_model=ExpenseOut, status_code=201)
async def create_expense(
    body: ExpenseCreate,
    group: Group = Depends(get_group_member),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExpenseOut:
    expense = await expenses_service.create_expense(db, group, user, body)
    return build_expense_out(expense)


@router.get("/expenses/{expense_id}", response_model=ExpenseOut)
async def get_expense(
    expense: Expense = Depends(get_expense_member),
) -> ExpenseOut:
    # Soft-deleted rows are returned too, with deleted_at set (§2.5).
    return build_expense_out(expense)


@router.patch("/expenses/{expense_id}", response_model=ExpenseOut)
async def update_expense(
    body: ExpenseUpdate,
    expense: Expense = Depends(get_expense_member),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExpenseOut:
    updated = await expenses_service.update_expense(db, expense, user, body)
    return build_expense_out(updated)


@router.delete("/expenses/{expense_id}", status_code=204)
async def delete_expense(
    expense: Expense = Depends(get_expense_member),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await expenses_service.delete_expense(db, expense, user)


@router.post("/expenses/{expense_id}/receipt", response_model=AttachmentOut, status_code=201)
async def upload_receipt(
    file: UploadFile = File(...),
    expense: Expense = Depends(get_expense_member),
    db: AsyncSession = Depends(get_db),
) -> AttachmentOut:
    data = await file.read()
    attachment = await expenses_service.upload_receipt(
        db, expense, file.filename or "receipt", file.content_type or "", data
    )
    return build_attachment_out(attachment)


@router.delete("/expenses/{expense_id}/receipt", status_code=204)
async def delete_receipt(
    expense: Expense = Depends(get_expense_member),
    db: AsyncSession = Depends(get_db),
) -> None:
    await expenses_service.delete_receipt(db, expense)


@router.get("/attachments/{attachment_id}")
async def download_attachment(
    attachment: Attachment = Depends(get_attachment_member),
    db: AsyncSession = Depends(get_db),
) -> Response:
    attachment, data = await expenses_service.read_attachment(db, attachment)
    return Response(
        content=data,
        media_type=attachment.content_type,
        headers={"Content-Disposition": f'attachment; filename="{attachment.filename}"'},
    )
