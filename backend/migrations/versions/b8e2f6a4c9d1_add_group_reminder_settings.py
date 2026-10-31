"""add per-group reminder settings (nullable overrides on groups)

Revision ID: b8e2f6a4c9d1
Revises: d4a7f1c9e2b5
Create Date: 2026-10-31 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b8e2f6a4c9d1"
down_revision = "d4a7f1c9e2b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "groups",
        sa.Column("debt_overdue_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "groups",
        sa.Column("reminder_interval_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("groups", "reminder_interval_days")
    op.drop_column("groups", "debt_overdue_days")
