"""Obuna klaviaturasi."""

from __future__ import annotations

from types import ModuleType
from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import Channel


def get_subscription_keyboard(
    t: ModuleType, channels: List[Channel]
) -> InlineKeyboardMarkup:
    """Majburiy obuna kanallari + tekshirish tugmasi.

    Kanal nomlari adminlar kiritgan matn — tarjima qilinmaydi, faqat
    tekshirish tugmasi interfeys tilida bo'ladi.
    """
    buttons = [
        [InlineKeyboardButton(text=channel.button_text, url=channel.button_url)]
        for channel in channels
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                text=t.BTN_CHECK_SUBSCRIPTION, callback_data="check_subscription"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)
