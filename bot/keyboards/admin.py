from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


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
    """Faol tarqatish yo'q paytdagi menyu — yangisini boshlash."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📨 Forward xabar yuborish"), KeyboardButton(text="📬 Oddiy xabar yuborish")],
            [KeyboardButton(text="🗂 Tarix")],
            [KeyboardButton(text="🔙 Orqaga")],
        ],
        resize_keyboard=True,
    )


def broadcast_active_keyboard(status: str) -> ReplyKeyboardMarkup:
    """Hozir ketayotgan/pauzadagi tarqatish uchun boshqaruv.

    Har doim FAQAT bitta faol tarqatish bo'lishi mumkin (yangisi
    boshlashdan oldin tekshiriladi) — shuning uchun tugmalar biror ID'ga
    emas, "hozirgi faol tarqatish"ga ishlaydi, alohida tanlash shart emas.
    """
    rows: list[list[KeyboardButton]] = []
    if status == "running":
        rows.append([KeyboardButton(text="⏸ Pauza")])
    elif status == "paused":
        rows.append([KeyboardButton(text="▶️ Davom ettirish")])
    if status in ("running", "pause_requested", "paused"):
        rows.append([KeyboardButton(text="⛔ Bekor qilish")])
    rows.append([KeyboardButton(text="🔄 Yangilash")])
    rows.append([KeyboardButton(text="🔙 Orqaga")])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def broadcast_test_confirm_keyboard() -> ReplyKeyboardMarkup:
    """Sinov xabari (faqat adminga) yuborilgandan keyingi yakuniy tasdiq."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Ha, hammaga yuborilsin")],
            [KeyboardButton(text="🔁 Boshqa xabar")],
            [KeyboardButton(text="🔙 Orqaga")],
        ],
        resize_keyboard=True,
    )


def broadcast_peak_confirm_keyboard() -> ReplyKeyboardMarkup:
    """Eng band vaqtda (18:00-23:00) qo'shimcha tasdiq."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ Ha, baribir yubor")],
            [KeyboardButton(text="⛔ Bekor qilish")],
        ],
        resize_keyboard=True,
    )


def back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Orqaga")]],
        resize_keyboard=True,
    )
