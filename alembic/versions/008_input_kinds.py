"""`translations.input_kind` ro'yxatini kengaytirish

Revision ID: 008
Revises: 007
Create Date: 2026-07-31 09:30:00.000000

Bot endi faqat matnni emas, media izohlarini, "post" (`rich_message`),
so'rovnoma va checklist matnlarini ham tarjima qiladi. Izoh qaysi media
ostida turgani ML tahlili uchun ahamiyatli, shuning uchun umumiy "caption"
emas, aniq turi yoziladi.

`caption` ham qoldirilgan — media turi aniqlanmagan holat uchun zaxira.
"""
from alembic import op


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

KINDS = (
    # mavjudlari
    "text", "voice", "photo", "document", "forward", "inline",
    # yangi: media izohlari
    "caption", "video", "audio", "animation", "video_note", "paid_media",
    # yangi: matn saqlaydigan boshqa turlar
    "post", "poll", "checklist",
)


def _values(kinds) -> str:
    return ", ".join(f"'{kind}'" for kind in kinds)


def upgrade() -> None:
    op.execute("ALTER TABLE translations DROP CONSTRAINT ck_translations_input_kind")
    op.execute(
        "ALTER TABLE translations ADD CONSTRAINT ck_translations_input_kind "
        f"CHECK (input_kind IN ({_values(KINDS)}))"
    )


def downgrade() -> None:
    old = ("text", "voice", "photo", "document", "forward", "inline")
    # Eski ro'yxatga sig'maydigan yozuvlarni umumiy qiymatga o'tkazamiz,
    # aks holda cheklovni qaytarib bo'lmaydi.
    op.execute(
        f"UPDATE translations SET input_kind = 'text' WHERE input_kind NOT IN ({_values(old)})"
    )
    op.execute("ALTER TABLE translations DROP CONSTRAINT ck_translations_input_kind")
    op.execute(
        "ALTER TABLE translations ADD CONSTRAINT ck_translations_input_kind "
        f"CHECK (input_kind IN ({_values(old)}))"
    )
