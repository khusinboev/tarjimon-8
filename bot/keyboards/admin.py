from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


def admin_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📊 Statistika"), KeyboardButton(text="🔧 Kanallar")],
            [KeyboardButton(text="📤 Reklama"), KeyboardButton(text="👤 Foydalanuvchilar")],
            [KeyboardButton(text="📜 Audit"), KeyboardButton(text="⚙️ Umumiy limitlar")],
        ],
        resize_keyboard=True,
    )


def admin_global_limits_keyboard() -> ReplyKeyboardMarkup:
    """Admin panelidan sozlanadigan umumiy (hamma uchun) standart limitlar."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✏️ Matn limiti"), KeyboardButton(text="✏️ Ovoz limiti")],
            [KeyboardButton(text="✏️ Rasm (bepul)"), KeyboardButton(text="✏️ Rasm (VIP)")],
            [KeyboardButton(text="🔙 Orqaga")],
        ],
        resize_keyboard=True,
    )


def admin_users_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Qidirish")],
            [KeyboardButton(text="🔙 Orqaga")],
        ],
        resize_keyboard=True,
    )


def admin_user_actions_keyboard() -> ReplyKeyboardMarkup:
    """Tanlangan foydalanuvchi ustida amallar."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🚫 Bloklash"),
                KeyboardButton(text="✅ Blokdan chiqarish"),
            ],
            [
                KeyboardButton(text="🔢 Limit o'rnatish"),
                KeyboardButton(text="♻️ Limitni tozalash"),
            ],
            [
                KeyboardButton(text="🔊 Ovoz limiti"),
                KeyboardButton(text="🔇 Ovoz limitini tozalash"),
            ],
            [
                KeyboardButton(text="🖼 Rasm limiti"),
                KeyboardButton(text="🚫 Rasm limitini tozalash"),
            ],
            [KeyboardButton(text="🔄 Yangilash")],
            [KeyboardButton(text="🔙 Orqaga")],
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
            [KeyboardButton(text="📊 Holat"), KeyboardButton(text="⛔ To'xtatish")],
            [KeyboardButton(text="🔙 Orqaga")],
        ],
        resize_keyboard=True,
    )


def back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Orqaga")]],
        resize_keyboard=True,
    )


def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    """Cho'qqi soatda tarqatishni tasdiqlash."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Davom etamiz", callback_data="bc:confirm"),
                InlineKeyboardButton(text="⛔ Bekor", callback_data="bc:cancel"),
            ]
        ]
    )
