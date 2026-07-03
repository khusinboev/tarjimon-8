"""Initial full schema (create-only)

Revision ID: 001
Revises:
Create Date: 2026-07-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("telegram_id", sa.BigInteger(), unique=True, nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("language_code", sa.String(length=10), nullable=True),
        sa.Column("is_bot", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_interaction", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_users_telegram_id", "users", ["telegram_id"])
    op.create_index("idx_users_created_at", "users", ["created_at"])
    op.create_index("idx_users_last_interaction", "users", ["last_interaction"])
    op.create_index("idx_users_is_active", "users", ["is_active"])

    op.create_table(
        "channels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("channel_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("channel_username", sa.String(length=255), nullable=False, unique=True),
        sa.Column("channel_title", sa.String(length=255), nullable=False),
        sa.Column("button_text", sa.String(length=255), nullable=False),
        sa.Column("button_url", sa.String(length=512), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("added_by", sa.BigInteger(), sa.ForeignKey("users.telegram_id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "broadcasts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("content_preview", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="created"),
        sa.Column("total_targets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_broadcasts_created_by", "broadcasts", ["created_by"])
    op.create_index("idx_broadcasts_status", "broadcasts", ["status"])

    op.create_table(
        "broadcast_deliveries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("broadcast_id", sa.BigInteger(), sa.ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_broadcast_deliveries_broadcast_id", "broadcast_deliveries", ["broadcast_id"])
    op.create_index("idx_broadcast_deliveries_user_id", "broadcast_deliveries", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_broadcast_deliveries_user_id", table_name="broadcast_deliveries")
    op.drop_index("idx_broadcast_deliveries_broadcast_id", table_name="broadcast_deliveries")
    op.drop_table("broadcast_deliveries")

    op.drop_index("idx_broadcasts_status", table_name="broadcasts")
    op.drop_index("idx_broadcasts_created_by", table_name="broadcasts")
    op.drop_table("broadcasts")

    op.drop_table("channels")

    op.drop_index("idx_users_is_active", table_name="users")
    op.drop_index("idx_users_last_interaction", table_name="users")
    op.drop_index("idx_users_created_at", table_name="users")
    op.drop_index("idx_users_telegram_id", table_name="users")
    op.drop_table("users")
