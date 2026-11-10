"""Obuna klaviaturasi."""

from __future__ import annotations

from types import ModuleType
from typing import List

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.database.models import Channel


def support_reply_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    """Murojaat sarlavhasi ostidagi "↩️ Javob yozish" tugmasi.

    `target_user_id` — ICHKI `users.id` (Telegram ID emas): callback_data
    Telegram'ning 64 baytlik chegarasiga sig'ishi kerak, ichki id har doim
    qisqaroq va barqaror.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ Javob yozish", callback_data=f"sup:pick:{target_user_id}"
                )
            ]
        ]
    )


def contact_send_again_keyboard(t: ModuleType) -> InlineKeyboardMarkup:
    """Foydalanuvchining "yuborildi" tasdig'i ostidagi "🔁 Yana yuborish"
    tugmasi — yangi murojaat yozish tartibiga aniq (state emas, reply
    emas) qaytish yo'li. Tugma ESKI xabarlarda ham abadiy ishlaydi
    (Telegram inline tugmalarni muddatsiz saqlaydi), reply-havolasi
    kabi vaqt o'tishi bilan yo'qolmaydi.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t.CONTACT_SEND_AGAIN_BUTTON, callback_data="sup:again")]
        ]
    )


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
