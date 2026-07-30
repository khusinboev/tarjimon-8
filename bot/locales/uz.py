"""O'zbekcha interfeys. Barcha lokal fayllar shu faylning kalitlarini takrorlaydi.

Yangi satr qo'shsangiz — uni `en.py` va `id.py` ga ham qo'shing.
`bot/locales/__init__.py` import paytida mosligini tekshiradi va farq bo'lsa
xato beradi, ya'ni yarim tarjima qilingan holat sezilmay qolmaydi.
"""

CODE = "uz"
NAME = "O‘zbekcha"

# ── Reply-menyu tugmalari ────────────────────────────────────
BTN_LANGUAGES = "🌐 Tillar"
BTN_SETTINGS = "⚙️ Sozlamalar"
BTN_HELP = "ℹ️ Yordam"

# ── Inline tugmalar ──────────────────────────────────────────
BTN_VOICE = "🔊 Ovoz"
BTN_SWAP = "🔄 Almashtirish"
BTN_BACK = "⬅️ Ortga"
BTN_CHECK_SUBSCRIPTION = "✅ Obunani tekshirish"
BTN_TTS_ENABLED = "Ovoz tugmasi"
BTN_TTS_AUTO = "Avto-ovoz"
BTN_INTERFACE_LANG = "🗣 Interfeys tili"

# `auto` tilining nomi — bazada `name_native` bo'sh, chunki bu til emas.
AUTO_DETECT = "Avto aniqlash"

# ── Salomlashish ─────────────────────────────────────────────
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
    "• <b>⚙️ Sozlamalar</b> — ovoz va interfeys tili\n\n"
    "Tarjima ostidagi tugmalar:\n"
    "🔊 — matnni ovozda eshitish\n"
    "🔄 — yo'nalishni teskari almashtirish\n"
    "🌐 — tillarni tanlash\n\n"
    "Tarjima <code>shu ko'rinishda</code> yuboriladi — ustiga bosib nusxa olishingiz mumkin.\n\n"
    "Kunlik limit: <b>{limit}</b> ta tarjima."
)

# ── Tillar ───────────────────────────────────────────────────
LANGUAGE_MENU = (
    "🌐 <b>Tarjima yo'nalishi</b>\n\n"
    "Hozirgi: <b>{source} → {target}</b>\n\n"
    "O'zgartirish uchun tanlang:"
)

PICK_SOURCE = "🔤 <b>Manba tilni</b> tanlang (matn qaysi tilda):"
PICK_TARGET = "🎯 <b>Maqsad tilni</b> tanlang (qaysi tilga tarjima qilinsin):"
PICK_INTERFACE = "🗣 <b>Interfeys tilini</b> tanlang:"

LANG_SAVED = "✅ Yo'nalish: <b>{source} → {target}</b>"
LANG_SWAPPED = "🔄 Almashtirildi: <b>{source} → {target}</b>"
INTERFACE_SAVED = "✅ Interfeys tili: <b>{name}</b>"

# Manba til `auto` bo'lsa va matn tarjima qilinmagan bo'lsa almashtirish mumkin emas.
SWAP_NEEDS_SOURCE = (
    "Avto aniqlashni maqsad til qilib bo'lmaydi. Avval manba tilni tanlang."
)

# ── Sozlamalar ───────────────────────────────────────────────
SETTINGS = (
    "⚙️ <b>Sozlamalar</b>\n\n"
    "🔊 Ovoz tugmasi: <b>{tts}</b>\n"
    "🎧 Avto-ovoz: <b>{tts_auto}</b>\n"
    "🗣 Interfeys tili: <b>{interface}</b>\n\n"
    "Bugun ishlatilgan: <b>{used}/{limit}</b> tarjima"
)

ON = "yoqilgan ✅"
OFF = "o'chirilgan ❌"

# ── Obuna ────────────────────────────────────────────────────
SUBSCRIBE_REQUIRED = "❗️ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:"
SUBSCRIBE_OK = "✅ Rahmat! Endi botdan foydalanishingiz mumkin."
SUBSCRIBE_STILL_MISSING = "❌ Siz hali barcha kanallarga obuna bo'lmadingiz."
SEND_TEXT_PROMPT = "Tarjima qilish uchun matn yuboring."

# ── Limitlar va xatolar ──────────────────────────────────────
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

TRANSLATION_NOT_FOUND = "Bu tarjima topilmadi (eskirgan tugma)."
LANGUAGE_NOT_AVAILABLE = "Bu til mavjud emas"
