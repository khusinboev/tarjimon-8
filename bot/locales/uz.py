"""O'zbekcha interfeys. Barcha lokal fayllar shu faylning kalitlarini takrorlaydi.

Yangi satr qo'shsangiz — uni `en.py` va `id.py` ga ham qo'shing.
`bot/locales/__init__.py` import paytida mosligini tekshiradi va farq bo'lsa
xato beradi, ya'ni yarim tarjima qilingan holat sezilmay qolmaydi.
"""

CODE = "uz"
NAME = "O‘zbekcha"

# ── Reply-menyu tugmalari ────────────────────────────────────
BTN_LANGUAGES = "🌐 Tillar"
BTN_DONATE = "⭐ Homiylik"
BTN_CONTACT = "✉️ Adminga murojaat"
BTN_HELP = "ℹ️ Yordam"

# ── Inline tugmalar ──────────────────────────────────────────
BTN_VOICE = "🔊 Ovoz"
BTN_SWAP = "🔄 Almashtirish"
BTN_BACK = "⬅️ Ortga"
BTN_CANCEL = "❌ Bekor qilish"
BTN_CHECK_SUBSCRIPTION = "✅ Obunani tekshirish"

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
    "• <b>✉️ Adminga murojaat</b> — savol yoki taklif yuborish\n\n"
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

LANG_SAVED = "✅ Yo'nalish: <b>{source} → {target}</b>"
LANG_SWAPPED = "🔄 Almashtirildi: <b>{source} → {target}</b>"

# Manba til `auto` bo'lsa va matn tarjima qilinmagan bo'lsa almashtirish mumkin emas.
SWAP_NEEDS_SOURCE = (
    "Avto aniqlashni maqsad til qilib bo'lmaydi. Avval manba tilni tanlang."
)


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

# ── Adminga murojaat ─────────────────────────────────────────
CONTACT_PROMPT = (
    "✉️ <b>Adminga murojaat</b>\n\n"
    "Xabaringizni yozib yuboring — u to'g'ridan-to'g'ri adminga yetadi.\n\n"
    "<i>Bu yerda suhbat yo'q: siz yozasiz, admin o'qiydi. Javob kerak bo'lsa "
    "aloqa uchun ma'lumot qoldiring.</i>"
)
CONTACT_SENT = "✅ Xabaringiz adminga yuborildi. Rahmat!"
CONTACT_CANCELLED = "Bekor qilindi."
CONTACT_TOO_LONG = "📏 Xabar juda uzun (<b>{length}</b> belgi). Eng ko'pi: <b>{limit}</b>."
CONTACT_ONLY_TEXT = "✍️ Iltimos, xabarni matn ko'rinishida yuboring."
CONTACT_RATE_LIMITED = (
    "⏳ Siz yaqinda murojaat yubordingiz.\n\n"
    "Keyingi xabarni <b>{minutes}</b> daqiqadan keyin yuborishingiz mumkin."
)
CONTACT_FAILED = "⚠️ Xabarni yuborib bo'lmadi. Keyinroq urinib ko'ring."

# ── Homiylik (Telegram Stars) ────────────────────────────────
DONATE_INTRO = (
    "⭐ <b>Homiylik</b>\n\n"
    "Bot bepul va reklamasiz ishlaydi. Server va tarjima xizmatlari esa "
    "pul talab qiladi.\n\n"
    "Agar bot sizga foydali bo'lsa, Telegram Stars bilan qo'llab-quvvatlashingiz "
    "mumkin. Har qanday miqdor yordam beradi.\n\n"
    "Quyidan miqdorni tanlang:"
)
DONATE_INVOICE_TITLE = "Tarjimon botga homiylik"
DONATE_INVOICE_DESC = "{stars} ⭐ — botni qo'llab-quvvatlash uchun rahmat!"
DONATE_THANKS = (
    "❤️ <b>Rahmat!</b>\n\n"
    "Siz <b>{stars} ⭐</b> homiylik qildingiz. Bu bot ishlab turishiga "
    "to'g'ridan-to'g'ri yordam beradi.\n\n"
    "Jamg'armangiz uchun katta rahmat!"
)
DONATE_FAILED = "⚠️ To'lovni boshlab bo'lmadi. Keyinroq urinib ko'ring."
DONATE_CUSTOM = "✏️ Boshqa miqdor"
DONATE_CUSTOM_PROMPT = (
    "✏️ Necha ⭐ homiylik qilmoqchisiz?\n\n"
    "Sonni yozib yuboring ({min}–{max})."
)
DONATE_CUSTOM_INVALID = "❌ Faqat son yuboring, {min} dan {max} gacha."

# ── Qayta ishga tushish xabari (scripts/broadcast_relaunch.py) ──
RELAUNCH = (
    "🎉 <b>Bot yangilandi va qayta ishga tushdi!</b>\n\n"
    "Salom! Botni to'liq qayta yozdik — endi tezroq, barqarorroq va qulayroq.\n\n"
    "<b>Nima yangi:</b>\n\n"
    "📋 <b>Nusxa olish oson</b> — tarjima ustiga bosasiz, matn nusxalanadi\n"
    "🔄 <b>Almashtirish tugmasi</b> — yo'nalishni bir bosishda teskari qilasiz\n"
    "🔊 <b>Ovoz</b> — tarjimani tinglashingiz mumkin\n"
    "🌐 <b>21 til</b> — o'zbek, rus, ingliz, turk, arab va boshqalar\n"
    "⚡️ <b>Tezlik</b> — takroriy tarjimalar bir zumda qaytadi\n"
    "🗣 <b>Interfeys tilingizda</b> — Telegram tilingizga qarab avtomatik\n\n"
    "Sinab ko'rish uchun shunchaki matn yuboring 👇"
)
