"""Amhar tilini qo'shish

Revision ID: 006
Revises: 005
Create Date: 2026-07-30 14:20:00.000000

Tekshirilgan:
    deep-translator  — `am` qo'llab-quvvatlanadi (Google: "amharic")
    edge-tts         — `am-ET-MekdesNeural` (ayol), `am-ET-AmehaNeural` (erkak)

`sort_order` 10 qo'yilgan — `az` bilan bir xil. Ro'yxat `ORDER BY sort_order,
code` bilan olinadi, ya'ni ikkilamchi tartib alifbo bo'yicha va `am` `az` dan
oldin chiqadi. Boshqa 12 ta qatorni qayta raqamlash shart emas.
"""
from alembic import op
import sqlalchemy as sa


revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO languages (
                code, name_uz, name_en, name_native, flag,
                tts_voice, supports_tts, supports_detection, is_active, sort_order
            )
            VALUES (
                'am', 'Amhar', 'Amharic', 'አማርኛ', '🇪🇹',
                'am-ET-MekdesNeural', true, true, true, 10
            )
            ON CONFLICT (code) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM languages WHERE code = 'am'"))
