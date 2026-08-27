"""Admin "javob yozish" tugmasi bosilganda tanlangan suhbat — `admin_reply_targets`

Revision ID: 016
Revises: 015
Create Date: 2026-08-27 03:00:00.000000

Real production voqea (2026-08-27): admin eski (bir necha soat oldingi)
murojaatga Telegram'ning "reply" (swipe) mexanizmi bilan javob berishga
urindi, lekin Telegram bu holatda `reply_to_message`ni HAR DOIM HAM
uzatavermas ekan (hujjatlashtirilgan cheklovdan qat'i nazar) — natijada
javob yetkazilish o'rniga oddiy tarjima sifatida qayta ishlangan.

Shu sababli endi har bir murojaat sarlavhasida "↩️ Javob yozish" tugmasi
bor. Bosilsa, shu jadvalga (admin_chat_id -> target_user_id) yoziladi —
FSM EMAS, muddatsiz, faqat ISHLATILGANDA (keyingi oddiy xabar
yuborilganda) o'chiriladi.
"""
from alembic import op
import sqlalchemy as sa


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_reply_targets",
        sa.Column("admin_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "set_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("admin_chat_id"),
    )


def downgrade() -> None:
    op.drop_table("admin_reply_targets")
