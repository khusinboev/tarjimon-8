"""OCR.Space ko'p-kalitli navbat: qaysi kalit ishlatilganini yozib borish.

Har bir OCR.Space kaliti alohida hisobga (email) tegishli va o'z bepul
oylik hajmiga (odatda 25 000) ega. Bir nechta kalit sozlansa (`OCRSPACE_API_KEYS`),
`bot/services/ocr.py` navbat bilan ishlatadi va har biri o'z hajmidan
oshmasligini nazorat qiladi — buning uchun har bir muvaffaqiyatli chaqiruvda
QAYSI kalit ishlatilgani (ro'yxatdagi tartib raqami) yozilishi kerak.
"""

from alembic import op
import sqlalchemy as sa


revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("translations", sa.Column("ocr_key_index", sa.Integer()))

    # Migratsiya 011 dagi indeks endi `ocr_key_index`ni ham qamrab olishi
    # kerak — har bir kalitning oylik hajmi alohida hisoblanadi.
    op.drop_index("idx_translations_ocr_monthly", table_name="translations")
    op.create_index(
        "idx_translations_ocr_monthly",
        "translations",
        ["input_kind", "ocr_provider", "ocr_key_index", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_translations_ocr_monthly", table_name="translations")
    op.create_index(
        "idx_translations_ocr_monthly",
        "translations",
        ["input_kind", "ocr_provider", "created_at"],
    )
    op.drop_column("translations", "ocr_key_index")
