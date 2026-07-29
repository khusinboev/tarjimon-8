"""Foydalanuvchi klaviaturalari."""

from __future__ import annotations

from typing import List, Optional, Sequence

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from bot.database.models import Language

# Reply-menyu tugmalari. Handlerlar shu konstantalar bo'yicha filtrlaydi —
# matnni ikki joyda takrorlamaslik uchun.
BTN_LANGUAGES = "🌐 Tillar"
BTN_HISTORY = "📜 Tarix"
BTN_SETTINGS = "⚙️ Sozlamalar"
BTN_HELP = "ℹ️ Yordam"

MENU_BUTTONS = frozenset({BTN_LANGUAGES, BTN_HISTORY, BTN_SETTINGS, BTN_HELP})


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_LANGUAGES), KeyboardButton(text=BTN_HISTORY)],
            [KeyboardButton(text=BTN_SETTINGS), KeyboardButton(text=BTN_HELP)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def language_menu(source: Language, target: Language) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔤 {source.flag} {source.name_uz}", callback_data="lang:pick:source"
                ),
                InlineKeyboardButton(
                    text=f"🎯 {target.flag} {target.name_uz}", callback_data="lang:pick:target"
                ),
            ],
            [InlineKeyboardButton(text="🔄 Almashtirish", callback_data="lang:swap")],
        ]
    )


def language_picker(
    languages: Sequence[Language], slot: str, *, per_row: int = 3
) -> InlineKeyboardMarkup:
    """Tillar ro'yxati. `slot` — `source` yoki `target`."""
    rows: List[List[InlineKeyboardButton]] = []
    row: List[InlineKeyboardButton] = []

    for lang in languages:
        row.append(
            InlineKeyboardButton(
                text=f"{lang.flag} {lang.name_uz}",
                callback_data=f"lang:set:{slot}:{lang.code}",
            )
        )
        if len(row) == per_row:
            rows.append(row)
            row = []

    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton(text="⬅️ Ortga", callback_data="lang:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def translation_actions(
    translation_id: int, *, has_tts: bool, is_favorite: bool = False
) -> Optional[InlineKeyboardMarkup]:
    """Tarjima ostidagi tugmalar.

    Har bir bosish `translation_signals` ga yoziladi — bu tarjima sifatini
    o'lchashning yagona manbai.
    """
    buttons: List[InlineKeyboardButton] = []

    if has_tts:
        buttons.append(
            InlineKeyboardButton(text="🔊 Ovoz", callback_data=f"tr:tts:{translation_id}")
        )

    buttons.append(
        InlineKeyboardButton(
            text="⭐" if is_favorite else "☆",
            callback_data=f"tr:fav:{translation_id}",
        )
    )

    if not buttons:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def settings_menu(
    *, tts_enabled: bool, tts_auto: bool, save_history: bool
) -> InlineKeyboardMarkup:
    def mark(value: bool) -> str:
        return "✅" if value else "❌"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{mark(tts_enabled)} Ovoz tugmasi",
                    callback_data="set:toggle:tts_enabled",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{mark(tts_auto)} Avto-ovoz",
                    callback_data="set:toggle:tts_auto",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{mark(save_history)} Tarixni saqlash",
                    callback_data="set:toggle:save_history",
                )
            ],
        ]
    )


def history_nav(page: int, pages: int, *, mode: str = "history") -> InlineKeyboardMarkup:
    """`mode` — `history` yoki `favorites`."""
    nav: List[InlineKeyboardButton] = []

    if page > 1:
        nav.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"hist:{mode}:{page - 1}")
        )
    nav.append(
        InlineKeyboardButton(text=f"{page}/{pages}", callback_data="noop")
    )
    if page < pages:
        nav.append(
            InlineKeyboardButton(text="➡️", callback_data=f"hist:{mode}:{page + 1}")
        )

    other = "favorites" if mode == "history" else "history"
    other_label = "⭐ Sevimlilar" if mode == "history" else "📜 Tarix"

    return InlineKeyboardMarkup(
        inline_keyboard=[nav, [InlineKeyboardButton(text=other_label, callback_data=f"hist:{other}:1")]]
    )
