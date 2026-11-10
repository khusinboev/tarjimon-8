"""Google Translate / Azure Translator ko'p kalitli navbat.

`translations.provider_key_index` — `ocr_key_index`ga o'xshash (migratsiya
012): `provider='google_translate'`/`'azure_translator'` bo'lganda, o'sha
provayderning qaysi kaliti (`settings.GOOGLE_TRANSLATE_KEYS`/
`AZURE_TRANSLATOR_KEYS` ro'yxatidagi tartib raqami, 0-based) ishlatilganini
bildiradi. Har bir kalit alohida hisob/loyihaga tegishli bo'lishi mumkin —
har birining o'z oylik bepul belgi hajmi bor, shuning uchun har biri uchun
ALOHIDA hisob yuritish kerak (bitta GROUP BY so'rov bilan, `ocr.py` dagi
naqsh takrorlangan).
"""

from alembic import op
import sqlalchemy as sa


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("translations", sa.Column("provider_key_index", sa.Integer()))
    op.create_index(
        "idx_translations_provider_monthly",
        "translations",
        ["provider", "provider_key_index", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_translations_provider_monthly", table_name="translations")
    op.drop_column("translations", "provider_key_index")
