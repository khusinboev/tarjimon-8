"""Oromo tilini qo'shish

Revision ID: 009
Revises: 008
Create Date: 2026-07-31 10:15:00.000000

Tekshirilgan:
    deep-translator — `om` qo'llab-quvvatlanadi, ikki tomonlama ishlaydi
    edge-tts        — Oromo uchun ovoz YO'Q, shuning uchun `supports_tts=false`

`sort_order` 30 — ovozsiz tillar guruhi (ky, tg, tk bilan bir qatorda).
Ro'yxat `ORDER BY sort_order, code` bilan olingani uchun `om` shu guruh
ichida alifbo bo'yicha `ky` dan keyin, `tg` dan oldin turadi.
"""
from alembic import op
import sqlalchemy as sa


revision = "009"
down_revision = "008"
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
                'om', 'Oromo', 'Oromo', 'Afaan Oromoo', '🇪🇹',
                NULL, false, true, true, 30
            )
            ON CONFLICT (code) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM languages WHERE code = 'om'"))
