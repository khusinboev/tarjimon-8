"""interface_lang ni telegram_lang dan to'ldirish

Revision ID: 004
Revises: 003
Create Date: 2026-07-30 01:30:00.000000

Nima uchun kerak:
    Migratsiya paytida `user_settings.interface_lang` hamma uchun standart
    qiymat — `'uz'` bo'lib qoldi (37 443 qatorda). Interfeys uch tilli
    bo'lgandan keyin bu 17 866 ta o'zbek bo'lmagan foydalanuvchiga
    o'zbekcha interfeys ko'rsatardi.

    Qoida `bot/locales/resolve()` bilan aynan bir xil bo'lishi shart:
        telegram_lang `uz*` → uz
        telegram_lang `id*` → id
        qolgan hammasi      → en
"""
from alembic import op


revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Telegram `en-US`, `pt-br` kabi mintaqa qo'shimchali kodlar yuboradi,
    # shuning uchun boshlanishi bo'yicha solishtiramiz.
    op.execute(
        """
        UPDATE user_settings AS us
        SET interface_lang = CASE
                WHEN lower(u.telegram_lang) LIKE 'uz%' THEN 'uz'
                WHEN lower(u.telegram_lang) LIKE 'id%' THEN 'id'
                ELSE 'en'
            END
        FROM users AS u
        WHERE u.id = us.user_id
        """
    )

    # Standart qiymatni ham `en` ga o'zgartiramiz: yangi user uchun kod
    # `locales.resolve()` bilan aniq qiymat yozadi, lekin xom SQL bilan
    # qator qo'shilsa mantiqiy zaxira `en` bo'lishi kerak.
    op.execute("ALTER TABLE user_settings ALTER COLUMN interface_lang SET DEFAULT 'en'")


def downgrade() -> None:
    op.execute("ALTER TABLE user_settings ALTER COLUMN interface_lang SET DEFAULT 'uz'")
    op.execute("UPDATE user_settings SET interface_lang = 'uz'")
