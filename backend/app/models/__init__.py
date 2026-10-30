"""ORM models (one module per aggregate; see AGENTS.md §3.1).

Importing this package registers every table with ``Base.metadata`` so that
Alembic autogenerate and metadata-driven DDL see the full schema.
"""

from __future__ import annotations

from app.models.activity import ActivityEvent
from app.models.attachment import Attachment
from app.models.debt_reminder import DebtReminder
from app.models.email_token import EmailToken
from app.models.expense import Expense
from app.models.expense_participant import ExpenseParticipant
from app.models.group import Group
from app.models.invitation import Invitation
from app.models.membership import Membership
from app.models.notification import Notification
from app.models.push_subscription import PushSubscription
from app.models.settlement import Settlement
from app.models.token import RefreshToken
from app.models.user import User

__all__ = [
    "ActivityEvent",
    "Attachment",
    "DebtReminder",
    "EmailToken",
    "Expense",
    "ExpenseParticipant",
    "Group",
    "Invitation",
    "Membership",
    "Notification",
    "PushSubscription",
    "RefreshToken",
    "Settlement",
    "User",
]
