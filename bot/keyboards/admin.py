from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def admin_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="🔧 Kanallar")],
            [KeyboardButton(text="📤 Reklama")],
        ],
        resize_keyboard=True,
    )


def admin_channels_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➕ Kanal qo'shish"), KeyboardButton(text="❌ Kanalni olib tashlash")],
            [KeyboardButton(text="📋 Kanallar ro'yxati")],
            [KeyboardButton(text="🔙 Orqaga")],
        ],
        resize_keyboard=True,
    )


def admin_broadcast_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📨 Forward xabar yuborish"), KeyboardButton(text="📬 Oddiy xabar yuborish")],
            [KeyboardButton(text="⛔ Broadcastni to'xtatish")],
            [KeyboardButton(text="🔙 Orqaga")],
        ],
        resize_keyboard=True,
    )


def back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Orqaga")]],
        resize_keyboard=True,
    )
