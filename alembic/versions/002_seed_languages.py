"""Tillar ma'lumotnomasini to'ldirish

Ro'yxat ishlab turgan `tarjimon` bazasidan olingan (21 ta, `auto` bilan birga).
`tts_voice` qiymatlari edge-tts ovozlar ro'yxatidan tekshirib olingan — taxmin emas.
ky/tg/tk uchun edge-tts da ovoz yo'q, shuning uchun `supports_tts = false`.

Revision ID: 002
Revises: 001
"""

import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


# (code, name_uz, name_en, name_native, flag, tts_voice, sort_order)
# tts_voice=None → supports_tts=False
LANGUAGES = [
    ("auto", "Avto aniqlash", "Auto detect", None, "🌐", None, 0),
    ("uz", "O‘zbek", "Uzbek", "O‘zbekcha", "🇺🇿", "uz-UZ-MadinaNeural", 1),
    ("ru", "Rus", "Russian", "Русский", "🇷🇺", "ru-RU-SvetlanaNeural", 2),
    ("en", "Ingliz", "English", "English", "🇬🇧", "en-US-AvaNeural", 3),
    ("tr", "Turk", "Turkish", "Türkçe", "🇹🇷", "tr-TR-EmelNeural", 4),
    ("ar", "Arab", "Arabic", "العربية", "🇸🇦", "ar-SA-ZariyahNeural", 5),
    ("az", "Ozarbayjon", "Azerbaijani", "Azərbaycan", "🇦🇿", "az-AZ-BanuNeural", 10),
    ("de", "Nemis", "German", "Deutsch", "🇩🇪", "de-DE-SeraphinaMultilingualNeural", 11),
    ("es", "Ispan", "Spanish", "Español", "🇪🇸", "es-ES-XimenaNeural", 12),
    ("fa", "Fors", "Persian", "فارسی", "🇮🇷", "fa-IR-DilaraNeural", 13),
    ("fr", "Fransuz", "French", "Français", "🇫🇷", "fr-FR-VivienneMultilingualNeural", 14),
    ("hi", "Hind", "Hindi", "हिन्दी", "🇮🇳", "hi-IN-SwaraNeural", 15),
    ("id", "Indonez", "Indonesian", "Indonesia", "🇮🇩", "id-ID-GadisNeural", 16),
    ("it", "Italyan", "Italian", "Italiano", "🇮🇹", "it-IT-ElsaNeural", 17),
    ("ja", "Yapon", "Japanese", "日本語", "🇯🇵", "ja-JP-NanamiNeural", 18),
    ("kk", "Qozoq", "Kazakh", "Қазақша", "🇰🇿", "kk-KZ-AigulNeural", 19),
    ("ko", "Koreys", "Korean", "한국어", "🇰🇷", "ko-KR-SunHiNeural", 20),
    ("zh", "Xitoy", "Chinese", "中文", "🇨🇳", "zh-CN-XiaoxiaoNeural", 21),
    # edge-tts da ovoz yo'q — tarjima ishlaydi, ovoz tugmasi ko'rsatilmaydi.
    ("ky", "Qirg‘iz", "Kyrgyz", "Кыргызча", "🇰🇬", None, 30),
    ("tg", "Tojik", "Tajik", "Тоҷикӣ", "🇹🇯", None, 31),
    ("tk", "Turkman", "Turkmen", "Türkmençe", "🇹🇲", None, 32),
]


def upgrade() -> None:
    languages = sa.table(
        "languages",
        sa.column("code", sa.String),
        sa.column("name_uz", sa.String),
        sa.column("name_en", sa.String),
        sa.column("name_native", sa.String),
        sa.column("flag", sa.String),
        sa.column("supports_tts", sa.Boolean),
        sa.column("supports_detection", sa.Boolean),
        sa.column("tts_voice", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )

    op.bulk_insert(
        languages,
        [
            {
                "code": code,
                "name_uz": name_uz,
                "name_en": name_en,
                "name_native": name_native,
                "flag": flag,
                "supports_tts": voice is not None,
                # `auto` — psevdo-til, uni aniqlash mumkin emas.
                "supports_detection": code != "auto",
                "tts_voice": voice,
                "is_active": True,
                "sort_order": order,
            }
            for code, name_uz, name_en, name_native, flag, voice, order in LANGUAGES
        ],
    )


def downgrade() -> None:
    codes = tuple(row[0] for row in LANGUAGES)
    op.execute(
        sa.text("DELETE FROM languages WHERE code IN :codes").bindparams(
            sa.bindparam("codes", value=codes, expanding=True)
        )
    )
