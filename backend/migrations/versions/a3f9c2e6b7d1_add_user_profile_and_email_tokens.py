"""add user profile columns and email_tokens

Revision ID: a3f9c2e6b7d1
Revises: e25e6786133d
Create Date: 2026-10-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a3f9c2e6b7d1"
down_revision = "e25e6786133d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- new user columns (§2.13) ---
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "email_verified",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "preferred_language",
            sa.String(length=2),
            server_default="en",
            nullable=False,
        ),
    )
    op.add_column("users", sa.Column("avatar_data", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column("avatar_content_type", sa.String(length=128), nullable=True),
    )

    # --- email confirmation tokens (§2.13) ---
    op.create_table(
        "email_tokens",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("purpose", sa.String(length=8), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("target_email", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token"),
    )
    with op.batch_alter_table("email_tokens", schema=None) as batch_op:
        batch_op.create_index(
            "ix_email_tokens_user_purpose", ["user_id", "purpose"], unique=False
        )

    # --- bootstrap the first admin (lowest id) if none exists ---
    bind = op.get_bind()
    has_admin = bind.execute(
        sa.text("SELECT 1 FROM users WHERE is_admin = true LIMIT 1")
    ).first()
    if not has_admin:
        min_id = bind.execute(
            sa.text("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        ).scalar()
        if min_id is not None:
            bind.execute(
                sa.text("UPDATE users SET is_admin = true WHERE id = :id"),
                {"id": min_id},
            )


def downgrade() -> None:
    bind = op.get_bind()
    # Remove the backfilled admin flag before dropping the column.
    bind.execute(sa.text("UPDATE users SET is_admin = false"))

    with op.batch_alter_table("email_tokens", schema=None) as batch_op:
        batch_op.drop_index("ix_email_tokens_user_purpose")

    op.drop_table("email_tokens")
    op.drop_column("users", "avatar_content_type")
    op.drop_column("users", "avatar_data")
    op.drop_column("users", "preferred_language")
    op.drop_column("users", "email_verified")
    op.drop_column("users", "is_admin")
