"""Telegram Stars homiyligi uchun `donations` jadvali

Revision ID: 005
Revises: 004
Create Date: 2026-07-30 03:10:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "donations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=10),
            server_default=sa.text("'XTR'"),
            nullable=False,
        ),
        # Unikal: Telegram bir to'lov haqida takroriy xabar yuborishi mumkin,
        # ikkinchi yozuv shu cheklovda to'xtaydi.
        sa.Column("telegram_payment_charge_id", sa.String(length=255), nullable=False),
        sa.Column("provider_payment_charge_id", sa.String(length=255), nullable=True),
        sa.Column("invoice_payload", sa.String(length=255), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'paid'"), nullable=False
        ),
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint("status IN ('paid', 'refunded')", name="ck_donations_status"),
        sa.CheckConstraint("stars > 0", name="ck_donations_stars_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_payment_charge_id"),
    )
    op.create_index(op.f("ix_donations_created_at"), "donations", ["created_at"])
    op.create_index(op.f("ix_donations_status"), "donations", ["status"])
    op.create_index(op.f("ix_donations_user_id"), "donations", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_donations_user_id"), table_name="donations")
    op.drop_index(op.f("ix_donations_status"), table_name="donations")
    op.drop_index(op.f("ix_donations_created_at"), table_name="donations")
    op.drop_table("donations")
