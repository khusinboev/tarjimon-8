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
    "• <b>⭐ Homiylik</b> — loyihani Telegram Stars bilan qo'llab-quvvatlash\n"
    "• <b>✉️ Adminga murojaat</b> — savol yoki taklif yuborish\n"
    "• <b>/invite</b> — do'stlaringizni taklif qiling, ikkalangiz VIP kun yutasiz\n"
    "• 🖼 Rasm yuboring — undagi matnni o'qib tarjima qilaman\n\n"
    "Tarjima ostidagi tugmalar:\n"
    "🔊 — matnni ovozda eshitish\n"
    "🔄 — yo'nalishni teskari almashtirish\n"
    "🌐 — tillarni tanlash\n\n"
    "Tarjima <code>shu ko'rinishda</code> yuboriladi — ustiga bosib nusxa olishingiz mumkin.\n\n"
    "Kunlik limit: <b>{limit}</b> ta tarjima.\n\n"
    "👤 Turli murojaatlar uchun: @{admin}"
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

IMAGE_QUOTA_EXCEEDED = (
    "🚫 <b>Bugungi rasm limiti tugadi</b>\n\n"
    "Bugun {limit} ta rasmni tarjima qildingiz.\n"
    "Limit har kuni yangilanadi — ertaga davom ettirishingiz mumkin."
)
IMAGE_OCR_UNAVAILABLE = "🖼 Rasmdan tarjima hozircha sozlanmoqda — tez orada ishga tushadi."
IMAGE_OCR_FAILED = "⚠️ Rasmni o'qib bo'lmadi. Aniqroq/yorug'roq rasm bilan qayta urinib ko'ring."
IMAGE_NO_TEXT_FOUND = "🔍 Rasmda matn topilmadi."

RATE_LIMITED = "⏳ Biroz sekinroq, iltimos. Bir necha soniyadan keyin urinib ko'ring."

BANNED = (
    "🚫 <b>Hisobingiz bloklangan</b>\n\n"
    "Botdan foydalanish vaqtincha yopilgan. Savollar uchun: @{admin}"
)

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
    "🔍 Bu xabarda tarjima qilinadigan matn yo'q.\n\n"
    "Matn yuboring yoki rasm/video/hujjatga <b>izoh</b> yozib yuboring — "
    "izohni ham tarjima qilaman."
)
NO_TEXT_FOUND = "🔍 Bu xabarda tarjima qilinadigan matn topilmadi."

TRANSLATION_NOT_FOUND = "Bu tarjima topilmadi (eskirgan tugma)."
LANGUAGE_NOT_AVAILABLE = "Bu til mavjud emas"

DEVELOPER = (
    "👨‍💻 <b>Bot dasturchisi</b>\n\n"
    "@{admin}\n\n"
    "Taklif, savol yoki xatolik haqida <b>✉️ Adminga murojaat</b> "
    "bo'limi orqali ham yozishingiz mumkin."
)

# ── Adminga murojaat ─────────────────────────────────────────
CONTACT_PROMPT = (
    "✉️ <b>Adminga murojaat</b>\n\n"
    "Xabaringizni yozing — u to'g'ridan-to'g'ri adminga yetadi.\n\n"
    "Matn, rasm, video, ovozli xabar, hujjat — istalgan turni yuborishingiz mumkin.\n\n"
    "<i>Admin javobi shu yerga keladi. Suhbatni davom ettirish uchun uning "
    "xabariga reply qiling — istalgan payt, qayta murojaat qilmasdan.</i>"
)
CONTACT_SENT = (
    "✅ <b>Xabaringiz adminga yuborildi</b>\n\n"
    "Javob shu yerga keladi. Unga reply qilib suhbatni davom ettirishingiz mumkin."
)
CONTACT_CANCELLED = "Bekor qilindi."
CONTACT_TOO_LONG = "📏 Xabar juda uzun (<b>{length}</b> belgi). Eng ko'pi: <b>{limit}</b>."
CONTACT_RATE_LIMITED = (
    "⏳ Siz yaqinda murojaat yubordingiz.\n\n"
    "Keyingi xabarni <b>{minutes}</b> daqiqadan keyin yuborishingiz mumkin."
)
CONTACT_FAILED = "⚠️ Xabarni yuborib bo'lmadi. Keyinroq urinib ko'ring."
CONTACT_REPLY_HEADER = (
    "✉️ <b>Admindan javob</b>\n\n"
    "<i>Javob berish uchun shu xabarga reply qiling.</i>"
)
CONTACT_REPLY_SENT = "✅ Javobingiz adminga yuborildi."

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
DONATE_THANKS_PREMIUM = (
    "❤️ <b>Rahmat!</b>\n\n"
    "Siz <b>{stars} ⭐</b> homiylik qildingiz va shu bilan <b>{days} kunlik VIP</b> "
    "maqomiga ega bo'ldingiz!\n\n"
    "💎 VIP paytida kunlik tarjima va ovoz limitlari cheksiz.\n"
    "⏳ Amal qilish muddati: <b>{until}</b> gacha.\n\n"
    "Jamg'armangiz uchun katta rahmat!"
)
DONATE_VIP_ACTIVE = "💎 <b>VIP faol</b> — <b>{until}</b> gacha kunlik limitlar cheksiz."
DONATE_FAILED = "⚠️ To'lovni boshlab bo'lmadi. Keyinroq urinib ko'ring."

# Limitga yetganda ko'rsatiladigan chegirmali, tayyor narxli taklif —
# umumiy homiylik narxidan mustaqil (`QUICK_VIP_STARS`/`QUICK_VIP_DAYS`).
QUICK_VIP_BUTTON = "🌟 {stars} ⭐ — {days} kun cheksiz"
DONATE_OTHER_BUTTON = "⭐ Boshqa summalar"
QUICK_VIP_INVOICE_TITLE = "Tezkor VIP"
QUICK_VIP_INVOICE_DESC = (
    "{stars} ⭐ — {days} kunlik VIP: matn tarjima cheksiz, ovoz cheksiz, "
    "rasm limiti kengaygan."
)
DONATE_CUSTOM = "✏️ Boshqa miqdor"
DONATE_CUSTOM_PROMPT = (
    "✏️ Necha ⭐ homiylik qilmoqchisiz?\n\n"
    "Sonni yozib yuboring ({min}–{max})."
)
DONATE_CUSTOM_INVALID = "❌ Faqat son yuboring, {min} dan {max} gacha."
DONATE_CARD_BUTTON = "💳 Karta orqali"
DONATE_CARDS = (
    "💳 <b>Karta orqali homiylik</b>\n\n"
    "Raqam ustiga bosing — nusxa olinadi.\n\n"
    "🔵 <b>VISA</b>\n<code>{visa}</code>\n\n"
    "🟢 <b>UzCard</b>\n<code>{uzcard}</code>\n\n"
    "👤 {holder}\n\n"
    "<i>Har qanday miqdor yordam beradi. Rahmat!</i>"
)

# ── Referal dasturi ───────────────────────────────────────────
INVITE_TEXT = (
    "🎁 <b>Do'stlaringizni taklif qiling</b>\n\n"
    "Shaxsiy havolangiz:\n<code>{link}</code>\n\n"
    "Havola orqali kirgan do'stingiz birinchi tarjimasini qilishi bilanoq:\n"
    "• sizga <b>{referrer_days} kun VIP</b>\n"
    "• unga <b>{welcome_days} kun VIP</b>\n\n"
    "Havolani istagancha odamga yuborishingiz mumkin — chegara yo'q."
)
REFERRAL_WELCOME_NOTICE = (
    "🎁 <b>Xush kelibsiz bonusi!</b>\n\n"
    "Taklif havolasi orqali kelganingiz uchun sizga <b>{days} kunlik VIP</b> berildi.\n"
    "⏳ Amal qilish muddati: <b>{until}</b> gacha."
)
REFERRAL_BONUS_NOTICE = (
    "🎉 <b>Referal bonusi!</b>\n\n"
    "Siz taklif qilgan do'stingiz botdan foydalanishni boshladi — sizga "
    "<b>{days} kunlik VIP</b> berildi.\n"
    "⏳ Amal qilish muddati: <b>{until}</b> gacha."
)

# ── Qayta ishga tushish xabari (scripts/broadcast_relaunch.py) ──
RELAUNCH = (
    "🎉 <b>Bot yangilandi va qayta ishga tushdi!</b>\n\n"
    "Salom! Botni to'liq qayta yozdik — endi tezroq, barqarorroq va qulayroq.\n\n"
    "<b>Nima yangi:</b>\n\n"
    "📋 <b>Nusxa olish oson</b> — tarjima ustiga bosasiz, matn nusxalanadi\n"
    "🔄 <b>Almashtirish tugmasi</b> — yo'nalishni bir bosishda teskari qilasiz\n"
    "🔊 <b>Ovoz</b> — tarjimani tinglashingiz mumkin\n"
    "🌐 <b>23 til</b> — o'zbek, rus, ingliz, turk, arab, amhar va boshqalar\n"
    "⚡️ <b>Tezlik</b> — takroriy tarjimalar bir zumda qaytadi\n"
    "🗣 <b>Interfeys tilingizda</b> — Telegram tilingizga qarab avtomatik\n\n"
    "Sinab ko'rish uchun shunchaki matn yuboring 👇"
)
