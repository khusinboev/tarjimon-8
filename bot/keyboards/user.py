"""Foydalanuvchi klaviaturalari.

Barcha funksiyalar birinchi argument sifatida `t` — lokal modulini oladi
(`bot/locales/`). Tugma matnlari shu yerdan olinadi, kodda qattiq yozilmaydi.

Til nomlari uchun `Language.name_native` ishlatiladi ("Русский", "Türkçe",
"O‘zbekcha"). Bu interfeys tilidan qat'i nazar to'g'ri ko'rinadi va har bir
lokal uchun 21 ta til nomini tarjima qilish kerak emas.
"""

from __future__ import annotations

from types import ModuleType
from typing import List, Optional, Sequence

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.database.models import Language


def lang_label(t: ModuleType, lang: Optional[Language]) -> str:
    """Til nomi: bayroq + o'z tilidagi nomi.

    `auto` — til emas, shuning uchun `name_native` bo'sh va nomi interfeys
    tilidan olinadi.
    """
    if lang is None:
        return "—"
    if lang.code == "auto":
        return f"{lang.flag} {t.AUTO_DETECT}"
    return f"{lang.flag} {lang.name_native or lang.name_en or lang.code}"


def direction_label(
    t: ModuleType, source: Optional[Language], target: Optional[Language]
) -> str:
    return f"{lang_label(t, source)} → {lang_label(t, target)}"


def main_menu(t: ModuleType) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t.BTN_LANGUAGES)],
            [KeyboardButton(text=t.BTN_SETTINGS), KeyboardButton(text=t.BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def language_menu(
    t: ModuleType, source: Optional[Language], target: Optional[Language]
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔤 {lang_label(t, source)}", callback_data="lang:pick:source"
                ),
                InlineKeyboardButton(
                    text=f"🎯 {lang_label(t, target)}", callback_data="lang:pick:target"
                ),
            ],
            [InlineKeyboardButton(text=t.BTN_SWAP, callback_data="lang:swap")],
        ]
    )


def language_picker(
    t: ModuleType, languages: Sequence[Language], slot: str, *, per_row: int = 2
) -> InlineKeyboardMarkup:
    """Tillar ro'yxati. `slot` — `source` yoki `target`."""
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []

    for lang in languages:
        row.append(
            InlineKeyboardButton(
                text=lang_label(t, lang),
                callback_data=f"lang:set:{slot}:{lang.code}",
            )
        )
        if len(row) == per_row:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text=t.BTN_BACK, callback_data="lang:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def translation_actions(
    t: ModuleType,
    translation_id: int,
    *,
    has_tts: bool,
    source: Optional[Language],
    target: Optional[Language],
) -> InlineKeyboardMarkup:
    """Tarjima ostidagi tugmalar.

    Ikkinchi qatordagi tugma hozirgi yo'nalishni **ko'rsatib turadi** va bosilsa
    tillarni tanlash menyusini ochadi. Almashtirishdan keyin shu tugma
    yangilanadi — natija ekranda qoladi, bir zumda o'chib ketadigan bildirishnoma
    emas.
    """
    top: List[InlineKeyboardButton] = []

    if has_tts:
        top.append(
            InlineKeyboardButton(
                text=t.BTN_VOICE, callback_data=f"tr:tts:{translation_id}"
            )
        )
    top.append(
        InlineKeyboardButton(text=t.BTN_SWAP, callback_data=f"tr:swap:{translation_id}")
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            top,
            [
                InlineKeyboardButton(
                    text=direction_label(t, source, target),
                    callback_data=f"tr:langs:{translation_id}",
                )
            ],
        ]
    )


def settings_menu(
    t: ModuleType, *, tts_enabled: bool
) -> InlineKeyboardMarkup:
    def mark(value: bool) -> str:
        return "✅" if value else "❌"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{mark(tts_enabled)} {t.BTN_TTS_ENABLED}",
                    callback_data="set:toggle:tts_enabled",
                )
            ],
            [
                InlineKeyboardButton(
                    text=t.BTN_INTERFACE_LANG, callback_data="set:interface"
                )
            ],
        ]
    )


def interface_picker(t: ModuleType, names: dict[str, str], current: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅ ' if code == current else ''}{name}",
                callback_data=f"set:interface:{code}",
            )
        ]
        for code, name in names.items()
    ]
    rows.append([InlineKeyboardButton(text=t.BTN_BACK, callback_data="set:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
