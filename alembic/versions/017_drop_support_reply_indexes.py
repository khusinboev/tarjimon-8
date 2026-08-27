"""Reply-asosidagi murojaat qidiruvi butunlay bekor qilindi — indekslar o'chiriladi

Revision ID: 017
Revises: 016
Create Date: 2026-08-27 04:30:00.000000

2026-08-27: butun murojaat/yozishma tizimi FAQAT ANIQ TUGMA orqali
ishlaydigan qilib qayta qurildi (Telegram `reply_to_message`ning eski
xabarlar uchun ishonchsizligi sabab). `SupportRepository.by_admin_message`/
`by_user_message` (bu ikki indeksdan foydalangan yagona so'rovlar) olib
tashlandi — indekslar endi maqsadsiz, faqat yozish tezligini pasaytiradi.

`support_messages` jadvalining o'zi (va `admin_message_id`/`user_message_id`
ustunlari) TARIX/audit uchun saqlanadi — faqat QIDIRUV indekslari o'chadi.
"""
from alembic import op


revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("idx_support_admin_msg", table_name="support_messages")
    op.drop_index("idx_support_user_msg", table_name="support_messages")


def downgrade() -> None:
    op.create_index(
        "idx_support_admin_msg", "support_messages", ["admin_chat_id", "admin_message_id"]
    )
    op.create_index(
        "idx_support_user_msg", "support_messages", ["user_chat_id", "user_message_id"]
    )
