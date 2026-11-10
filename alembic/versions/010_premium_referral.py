"""Homiylik/referal orqali VIP va TTS uchun alohida limit

Revision ID: 010
Revises: 009
Create Date: 2026-08-05 08:00:00.000000

Uch ustun `user_settings` ga qo'shiladi:

  premium_until            — bot ichidagi VIP muddati. `User.is_premium`
                              bilan aralashtirmaslik kerak — u Telegram'ning
                              o'z belgisi (Telegram Premium client), bu esa
                              bizning homiylik/referal tizimimiz.
  tts_limit_override        — `daily_limit_override` bilan bir xil mantiq,
                              lekin ovoz (TTS) kunlik limiti uchun.
  referral_bonus_granted    — referal bonusi allaqachon berilganmi. Bonus
                              faqat yangi userning birinchi muvaffaqiyatli
                              tarjimasidan keyin beriladi, shuning uchun
                              takroriy berilishning oldini olish kerak.
"""
from alembic import op
import sqlalchemy as sa


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_settings", sa.Column("tts_limit_override", sa.Integer(), nullable=True)
    )
    op.add_column(
        "user_settings",
        sa.Column("premium_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_settings",
        sa.Column(
            "referral_bonus_granted",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "referral_bonus_granted")
    op.drop_column("user_settings", "premium_until")
    op.drop_column("user_settings", "tts_limit_override")
