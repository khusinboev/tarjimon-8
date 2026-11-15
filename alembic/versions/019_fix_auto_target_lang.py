"""Maqsad tili `auto` bo'lib qolgan foydalanuvchi sozlamalarini tuzatish

Revision ID: 019
Revises: 018
Create Date: 2026-11-16 03:30:00.000000

Eski botda (2026-07 gacha) maqsad tilni ham `auto` qilish mumkin edi —
amalda inglizcha degani (tarixiy tarjimalar: o'zbekcha/indonezcha ->
inglizcha, inglizcha -> o'zgarishsiz). Yangi botda `auto` faqat manba til;
bunday sozlamali foydalanuvchining HAR tarjimasi yiqilardi (MyMemory 403
"'AUTO' IS AN INVALID TARGET LANGUAGE" yoki "provayder yo'q"). 2026-11-16
holati: 142 foydalanuvchi.

Qiymat eski ma'nosi bo'yicha: `en`; manba allaqachon `en` bo'lsa — standart
`uz` (`DEFAULT_TARGET_LANG`).

Orqaga qaytarish: qaysi qator `auto` bo'lgani saqlanmaydi, shuning uchun
downgrade — no-op. Kerak bo'lsa, server zaxirasidan
(`/root/backup-019-auto-target.csv`) qaytariladi.
"""
from alembic import op


revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE user_settings
           SET target_lang = CASE WHEN source_lang = 'en' THEN 'uz' ELSE 'en' END
         WHERE target_lang = 'auto'
        """
    )


def downgrade() -> None:
    pass
