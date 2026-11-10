"""Rasmdan tarjima (OCR) va admin-sozlanadigan umumiy limitlar.

Nima va nega:
  - `translations.ocr_provider` — rasm tarjimasida qaysi OCR provayder
    (ocrspace / google_vision) matnni ajratganini yozadi. `provider` ustuni
    o'zgarmaydi — u har doim TARJIMA dvigatelini (deep_translator) bildiradi,
    OCR butunlay boshqa bosqich.
  - `daily_usage.images_count` — kunlik rasm tarjimasi hisoblagichi, matn/ovoz
    hisoblagichlaridan (`translations_count`/`tts_count`) butunlay alohida
    o'q — rasm limiti matn limitini yemaydi.
  - `user_settings.image_limit_override` — admin panelidan alohida
    foydalanuvchiga qo'yiladigan rasm limiti, `daily_limit_override`/
    `tts_limit_override` bilan bir xil konvensiya (`NULL`=standart, `0`=cheksiz).
  - `system_settings` — yagona qatorli (id=1) jadval: admin panelidan
    o'zgartiriladigan UMUMIY (hamma uchun) standart limitlar. `NULL` — .env
    dagi standart qiymat ishlatiladi, aks holda shu yerdagi qiymat ustun.
    Alohida jadval (UserSettings kabi emas) — bitta qatorli konfiguratsiya,
    foydalanuvchiga bog'liq emas.
"""

from alembic import op
import sqlalchemy as sa


revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("translations", sa.Column("ocr_provider", sa.String(32)))
    op.add_column(
        "daily_usage",
        sa.Column(
            "images_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column("user_settings", sa.Column("image_limit_override", sa.Integer()))

    op.create_table(
        "system_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("daily_translation_limit", sa.Integer()),
        sa.Column("daily_tts_limit", sa.Integer()),
        sa.Column("daily_image_limit_free", sa.Integer()),
        sa.Column("daily_image_limit_vip", sa.Integer()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
    op.drop_column("user_settings", "image_limit_override")
    op.drop_column("daily_usage", "images_count")
    op.drop_column("translations", "ocr_provider")
