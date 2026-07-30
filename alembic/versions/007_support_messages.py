"""Adminga murojaat yozishmasi uchun `support_messages`

Revision ID: 007
Revises: 006
Create Date: 2026-07-30 15:00:00.000000

Suhbat Telegram'ning "reply" mexanizmi orqali boradi, FSM holati saqlanmaydi.
Shuning uchun har bir yetkazilgan xabarning ikki uchidagi `message_id` yoziladi
va javob kelganda `reply_to_message.message_id` bo'yicha ip topiladi.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "support_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("admin_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("admin_message_id", sa.BigInteger(), nullable=False),
        sa.Column("user_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint("direction IN ('in', 'out')", name="ck_support_direction"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_support_messages_created_at"), "support_messages", ["created_at"]
    )
    op.create_index(op.f("ix_support_messages_user_id"), "support_messages", ["user_id"])
    # Javobni topish uchun ikki yo'nalishdagi qidiruv.
    op.create_index(
        "idx_support_admin_msg", "support_messages", ["admin_chat_id", "admin_message_id"]
    )
    op.create_index(
        "idx_support_user_msg", "support_messages", ["user_chat_id", "user_message_id"]
    )


def downgrade() -> None:
    op.drop_table("support_messages")
