"""add users.preferred_currency

Revision ID: d4a7f1c9e2b5
Revises: c5e8a2b4d6f1
Create Date: 2026-10-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "d4a7f1c9e2b5"
down_revision = "c5e8a2b4d6f1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("preferred_currency", sa.String(length=3), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "preferred_currency")
