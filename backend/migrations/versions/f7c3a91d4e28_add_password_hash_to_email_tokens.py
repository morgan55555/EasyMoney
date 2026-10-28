"""add password_hash to email_tokens

Revision ID: f7c3a91d4e28
Revises: a3f9c2e6b7d1
Create Date: 2026-10-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f7c3a91d4e28"
down_revision = "a3f9c2e6b7d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Password-change confirmation tokens (§2.13): the pending new-password
    # hash rides on the token row and is applied to the user only on
    # confirmation. Same type as users.password_hash.
    op.add_column(
        "email_tokens",
        sa.Column("password_hash", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("email_tokens", "password_hash")
