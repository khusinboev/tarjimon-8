"""Foydalanuvchi yuborgan media fayl identifikatorlari saqlanadigan bo'ldi

Revision ID: 018
Revises: 017
Create Date: 2026-08-31 13:10:00.000000

Ilgari media faqat TUR sifatida qolardi (`translations.input_kind='photo'`,
`support_messages.text='[🖼 rasm]'`) — faylning o'zi esa hech qayerda
belgilanmasdi: rasm OCR uchun yuklab olinib, baytlari xotiradan yo'qolardi,
murojaatdagi media esa `copy_message` bilan Telegram ichida ko'chirilardi.
Ya'ni o'tmishdagi media'ni qaytarib topib bo'lmasdi.

Endi har bir media uchun IKKI identifikator yoziladi (izoh:
`bot/utils/media.py`): `file_id` — keyin qayta yuklab olish uchun,
`file_unique_id` — o'zgarmas, qidirish va dedup uchun. Qo'shimcha
`source_message_id` esa `chat_id` bilan birga manba xabarning aniq
manzilini beradi.

Indekslar QISMAN (`WHERE ... IS NOT NULL`): tarjimalarning ~99% i oddiy
matn, ularda bu ustunlar NULL va indeksga umuman tushmaydi.

Eski qatorlar NULL bo'lib qoladi — orqaga qaytib to'ldirish imkonsiz.
"""
import sqlalchemy as sa
from alembic import op


revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


_TRANSLATION_COLUMNS = (
    ("source_message_id", sa.BigInteger()),
    ("media_file_id", sa.Text()),
    ("media_file_unique_id", sa.String(64)),
    ("media_mime_type", sa.String(128)),
    ("media_file_size", sa.BigInteger()),
    ("media_file_name", sa.Text()),
)

_SUPPORT_COLUMNS = (
    ("media_kind", sa.String(20)),
    ("media_file_id", sa.Text()),
    ("media_file_unique_id", sa.String(64)),
    ("media_mime_type", sa.String(128)),
    ("media_file_size", sa.BigInteger()),
    ("media_file_name", sa.Text()),
)


def upgrade() -> None:
    for name, type_ in _TRANSLATION_COLUMNS:
        op.add_column("translations", sa.Column(name, type_, nullable=True))
    for name, type_ in _SUPPORT_COLUMNS:
        op.add_column("support_messages", sa.Column(name, type_, nullable=True))

    op.create_index(
        "idx_translations_media_unique",
        "translations",
        ["media_file_unique_id"],
        postgresql_where=sa.text("media_file_unique_id IS NOT NULL"),
    )
    op.create_index(
        "idx_support_media_unique",
        "support_messages",
        ["media_file_unique_id"],
        postgresql_where=sa.text("media_file_unique_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_support_media_unique", table_name="support_messages")
    op.drop_index("idx_translations_media_unique", table_name="translations")
    for name, _ in _SUPPORT_COLUMNS:
        op.drop_column("support_messages", name)
    for name, _ in _TRANSLATION_COLUMNS:
        op.drop_column("translations", name)
