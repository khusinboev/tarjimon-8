from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from typing import List
from bot.database.models import Channel


def get_subscription_keyboard(channels: List[Channel]) -> InlineKeyboardMarkup:
    """Create keyboard with subscription channels"""
    buttons = []

    for channel in channels:
        buttons.append([
            InlineKeyboardButton(
                text=channel.button_text,
                url=channel.button_url,
            )
        ])

    # Add check button
    buttons.append([
        InlineKeyboardButton(
            text="✅ Obunani tekshirish",
            callback_data="check_subscription"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
