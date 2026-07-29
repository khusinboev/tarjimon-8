"""Foydalanuvchiga ko'rinadigan matnlar.

Hozircha faqat o'zbekcha. `user_settings.interface_lang` sxemada bor —
ko'p tilli interfeys qo'shilganda bu modul til bo'yicha lug'atga aylanadi.
"""

WELCOME = (
    "👋 Assalomu alaykum, {name}!\n\n"
    "Men <b>tarjimon bot</b>man. Menga istalgan matn yuboring — men uni tarjima qilaman.\n\n"
    "🌐 Hozirgi yo'nalish: <b>{source} → {target}</b>\n"
    "Uni o'zgartirish uchun <b>🌐 Tillar</b> tugmasini bosing."
)

WELCOME_BACK = (
    "Xush kelibsiz, {name}!\n\n"
    "🌐 Yo'nalish: <b>{source} → {target}</b>\n"
    "Tarjima qilish uchun matn yuboring."
)

HELP = (
    "ℹ️ <b>Botdan foydalanish</b>\n\n"
    "• Istalgan matn yuboring — u avtomatik tarjima qilinadi\n"
    "• <b>🌐 Tillar</b> — tarjima yo'nalishini o'zgartirish\n"
    "• <b>📜 Tarix</b> — oxirgi tarjimalaringiz\n"
    "• <b>⚙️ Sozlamalar</b> — ovoz va maxfiylik\n\n"
    "Tarjima ostidagi tugmalar:\n"
    "🔊 — matnni ovozda eshitish\n"
    "⭐ — sevimlilarga qo'shish\n\n"
    "Kunlik limit: <b>{limit}</b> ta tarjima."
)

LANGUAGE_MENU = (
    "🌐 <b>Tarjima yo'nalishi</b>\n\n"
    "Hozirgi: <b>{source} → {target}</b>\n\n"
    "O'zgartirish uchun tanlang:"
)

PICK_SOURCE = "🔤 <b>Manba tilni</b> tanlang (matn qaysi tilda):"
PICK_TARGET = "🎯 <b>Maqsad tilni</b> tanlang (qaysi tilga tarjima qilinsin):"

LANG_SAVED = "✅ Yo'nalish: <b>{source} → {target}</b>"
LANG_SWAPPED = "🔄 Almashtirildi: <b>{source} → {target}</b>"

SETTINGS = (
    "⚙️ <b>Sozlamalar</b>\n\n"
    "🔊 Ovoz tugmasi: <b>{tts}</b>\n"
    "🎧 Avto-ovoz: <b>{tts_auto}</b>\n"
    "📜 Tarixni saqlash: <b>{history}</b>\n\n"
    "Bugun ishlatilgan: <b>{used}/{limit}</b> tarjima"
)

HISTORY_EMPTY = "📜 Tarixingiz hozircha bo'sh.\n\nBirorta matn yuboring — u shu yerda saqlanadi."
HISTORY_DISABLED = (
    "📜 Tarixni saqlash o'chirilgan.\n\n"
    "Uni <b>⚙️ Sozlamalar</b> bo'limidan yoqishingiz mumkin."
)
HISTORY_HEADER = "📜 <b>Oxirgi tarjimalar</b> ({page}/{pages})"

FAVORITES_EMPTY = "⭐ Sevimlilar ro'yxati bo'sh.\n\nTarjima ostidagi ⭐ tugmasi orqali qo'shing."
FAVORITES_HEADER = "⭐ <b>Sevimlilar</b> ({page}/{pages})"

SUBSCRIBE_REQUIRED = "❗️ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:"
SUBSCRIBE_OK = "✅ Rahmat! Endi botdan foydalanishingiz mumkin."
SUBSCRIBE_STILL_MISSING = "❌ Siz hali barcha kanallarga obuna bo'lmadingiz."

QUOTA_EXCEEDED = (
    "🚫 <b>Kunlik limit tugadi</b>\n\n"
    "Bugun {limit} ta tarjima ishlatdingiz.\n"
    "Limit har kuni yangilanadi — ertaga davom ettirishingiz mumkin."
)

TTS_QUOTA_EXCEEDED = "🚫 Bugungi ovoz limiti ({limit} ta) tugadi."

RATE_LIMITED = "⏳ Biroz sekinroq, iltimos. Bir necha soniyadan keyin urinib ko'ring."

TOO_LONG = "📏 Matn juda uzun (<b>{length}</b> belgi).\n\nEng ko'pi: <b>{limit}</b> belgi."

TTS_TOO_LONG = "📏 Ovoz uchun matn juda uzun. Eng ko'pi: <b>{limit}</b> belgi."

SAME_LANGUAGE = (
    "🤔 Manba va maqsad til bir xil (<b>{lang}</b>).\n\n"
    "🌐 Tillar bo'limidan yo'nalishni o'zgartiring."
)

NO_TTS_FOR_LANG = "🔇 <b>{lang}</b> tili uchun ovoz mavjud emas."

ERRORS = {
    "timeout": "⌛ Tarjima xizmati javob bermadi. Qaytadan urinib ko'ring.",
    "provider_rate_limited": "⏳ Tarjima xizmati band. Bir daqiqadan keyin urinib ko'ring.",
    "invalid_language": "🌐 Til noto'g'ri tanlangan. 🌐 Tillar bo'limidan qayta tanlang.",
    "empty_result": "😕 Tarjima qilib bo'lmadi. Matnni o'zgartirib ko'ring.",
    "empty_input": "✍️ Tarjima qilish uchun matn yuboring.",
}
ERROR_DEFAULT = "⚠️ Xatolik yuz berdi. Qaytadan urinib ko'ring."

TTS_ERRORS = {
    "timeout": "⌛ Ovoz tayyorlanmadi — xizmat javob bermadi.",
    "no_voice": "🔇 Bu til uchun ovoz mavjud emas.",
    "too_long": "📏 Matn ovoz uchun juda uzun.",
}
TTS_ERROR_DEFAULT = "⚠️ Ovozni tayyorlab bo'lmadi."

UNSUPPORTED_INPUT = (
    "📄 Hozircha faqat <b>matn</b> tarjimasi mavjud.\n\n"
    "Ovozli xabar va rasm tarjimasi keyingi yangilanishda qo'shiladi."
)

FAVORITE_ADDED = "⭐ Sevimlilarga qo'shildi"
FAVORITE_REMOVED = "☆ Sevimlilardan olib tashlandi"

TRANSLATION_NOT_FOUND = "Bu tarjima topilmadi (eskirgan tugma)."


def onoff(value: bool) -> str:
    return "yoqilgan ✅" if value else "o'chirilgan ❌"
