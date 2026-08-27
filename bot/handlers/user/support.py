"""Adminga murojaat va yozishma.

Suhbat Telegram'ning **reply** mexanizmi orqali boradi:
  foydalanuvchi murojaat yozadi  → admin chatiga tushadi
  admin o'sha xabarga reply qiladi → foydalanuvchiga yetadi
  foydalanuvchi javobga reply qiladi → adminga qaytadi

FSM holati saqlanmaydi — javob berilayotgan xabarning o'zi suhbatni
aniqlaydi. Har bir yetkazilgan xabar uchun ikki uchdagi `message_id`
`support_messages` ga yoziladi va ip shundan topiladi.

Ip topilmasa `SkipHandler` bilan keyingi handlerlarga o'tkaziladi: oddiy
reply bo'lsa matn odatdagidek tarjima qilinishi kerak.

**2026-08-27 qo'shimcha — "↩️ Javob yozish" tugmasi.** Telegram
`message.reply_to_message`ni ESKI xabarlar uchun har doim ham
uzatavermaydi (hujjatlashtirilgan cheklovdan qat'i nazar, amalda bir
necha soatdan keyin kuzatilgan haqiqiy voqea) — shu holatda reply
mexanizmining O'ZI ishlamay qoladi. Shuning uchun har bir murojaat
sarlavhasida tugma bor: bosilsa, `admin_reply_targets`ga (FSM EMAS,
oddiy DB qatori, muddatsiz) "tanlangan suhbat" yoziladi — reply ishlamasa
ham, KEYINGI oddiy xabar shu foydalanuvchiga boradi (bir martalik).

Har qanday tur uzatiladi — rasm, video, ovoz, hujjat, stiker. Buning uchun
`copy_message` ishlatiladi: media qayta yuklanmaydi, Telegram faylni o'zida
ko'chiradi. Har bir uzatishda ikki xabar ketadi — kim yozgani haqida
sarlavha va kontentning o'zi — va ikkalasi ham ipga bog'lanadi, shunda
qaysi biriga reply qilinsa ham suhbat topiladi.

Murojaat matni ikki joyga tushadi:
  1. Adminlarning Telegram chatiga — darhol ko'rish uchun
  2. `events` jadvaliga (`support.message_sent`) — admin o'tkazib yuborsa
     yoki Telegram yuborishda xato bo'lsa matn yo'qolmasligi uchun
"""

from __future__ import annotations

import logging
import re
from types import ModuleType

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import User
from bot.database.redis import atomic_rate_incr
from bot.database.repositories.support_repository import SupportRepository
from bot.keyboards.inline import support_reply_keyboard
from bot.keyboards.user import cancel_menu, main_menu
from bot import locales
from bot.locales import CANCEL_BUTTONS, CONTACT_BUTTONS, RESERVED_BUTTONS
from bot.services.events import EventService, EventType
from bot.states.support import SupportStates
from bot.utils.text import html_escape, truncate

logger = logging.getLogger(__name__)
router = Router(name="support")


async def _rate_limited(
    redis,
    user_id: int,
    *,
    limit: int | None = None,
    key: str = "support",
) -> int:
    """Limit oshgan bo'lsa qolgan daqiqalarni qaytaradi, aks holda 0.

    `key` alohida hisoblagichlar uchun: yangi murojaat va suhbat ichidagi
    javob har xil chegaraga ega bo'lishi kerak.

    Redis yo'q bo'lsa cheklov ishlamaydi (fail-open) — tarjima oqimidagi
    bilan bir xil qaror: Redis tushganda bot ishlashda davom etsin.
    """
    if not redis:
        return 0
    cap = limit if limit is not None else settings.SUPPORT_RATE_LIMIT
    redis_key = f"{key}:{user_id}"
    try:
        # Atomik Lua skript orqali — alohida incr+expire orasida uzilib
        # qolsa, kalit TTL'siz abadiy o'sib, foydalanuvchini doimiy
        # bloklab qo'yishi mumkin edi (izoh: `atomic_rate_incr`).
        count = await atomic_rate_incr(redis, redis_key, settings.SUPPORT_RATE_WINDOW)
        if count > cap:
            ttl = await redis.ttl(redis_key)
            return max(1, (ttl + 59) // 60) if ttl and ttl > 0 else 1
        return 0
    except Exception:
        logger.warning("Murojaat limitini tekshirib bo'lmadi", exc_info=True)
        return 0


def _admin_view(user: User, *, is_reply: bool = False) -> str:
    """Adminga ko'rinadigan sarlavha.

    Admin — bitta odam (egasi), shuning uchun bu matn tarjima qilinmaydi.
    Kontentning o'zi alohida xabar bo'lib keladi (`_deliver`), bu yerda
    faqat kim yozgani ko'rsatiladi.
    """
    username = f"@{user.username}" if user.username else "—"
    name = html_escape(user.first_name or "—")
    title = "💬 <b>Suhbat davomi</b>" if is_reply else "✉️ <b>Yangi murojaat</b>"
    return (
        f"{title}\n\n"
        f"👤 {name} · {username}\n"
        f"🆔 <code>{user.telegram_id}</code>\n"
        f"🌐 {user.telegram_lang or '—'}"
    )


# Matn bo'lmagan xabarlar uchun belgi. `support_messages.text` NOT NULL,
# shuning uchun bo'sh qoldirib bo'lmaydi — va admin jurnalda nima
# kelganini ko'rishi kerak.
_CONTENT_MARKERS = (
    ("photo", "🖼 rasm"),
    ("video", "🎬 video"),
    ("animation", "🎞 GIF"),
    ("voice", "🎤 ovozli xabar"),
    ("audio", "🎵 audio"),
    ("document", "📄 hujjat"),
    ("sticker", "🩶 stiker"),
    ("video_note", "⭕️ video xabar"),
    ("contact", "👤 kontakt"),
    ("location", "📍 joylashuv"),
    ("poll", "📊 so'rovnoma"),
    ("dice", "🎲 o'yin"),
)


def _describe(message: Message) -> str:
    """Yozuvga tushadigan matn: matn yoki izoh, bo'lmasa kontent turi."""
    text = (message.text or message.caption or "").strip()
    if text:
        return text
    for attr, label in _CONTENT_MARKERS:
        if getattr(message, attr, None):
            return f"[{label}]"
    return "[xabar]"


async def _deliver(
    message: Message, to_chat_id: int, header: str, *, header_markup=None
) -> tuple[int, int | None]:
    """Sarlavha + kontent nusxasini yuboradi. `(sarlavha_id, nusxa_id)`.

    `copy_message` har qanday turni ko'chiradi — rasm, video, ovoz, hujjat,
    stiker — va media qayta yuklanmaydi, Telegram faylni o'zida ko'chiradi.

    Ba'zi turlarni ko'chirib bo'lmaydi (masalan so'rovnoma). O'shanda faqat
    sarlavha ketadi va ikkinchi qiymat `None` bo'ladi — chaqiruvchi buni
    ko'rib foydalanuvchiga xabar beradi.

    `header_markup` — faqat ADMINGA yuborilganda beriladi ("↩️ Javob
    yozish" tugmasi); foydalanuvchiga yuborilgan javobda kerak emas.
    """
    head = await message.bot.send_message(to_chat_id, header, reply_markup=header_markup)
    try:
        copied = await message.bot.copy_message(
            chat_id=to_chat_id,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
        )
        return head.message_id, copied.message_id
    except Exception:
        logger.warning(
            "Kontentni ko'chirib bo'lmadi (chat=%s)", to_chat_id, exc_info=True
        )
        return head.message_id, None


# Sarlavhadagi `🆔 123456789` — suhbatni aniqlashning asosiy yo'li.
#
# Nega bazadagi qidiruv emas: admin eski xabarga ham javob bera olishi kerak,
# `support_messages` da esa faqat shu jadval paydo bo'lgandan keyingi
# xabarlar bor. Sarlavha matni xabarning o'zida turadi va hech qachon
# eskirmaydi. Baza qidiruvi zaxira bo'lib qoladi: admin kontent nusxasiga
# reply qilsa, unda sarlavha yo'q.
_ID_PATTERN = re.compile(r"🆔\s*(\d{5,})")

# `🆔` belgisi murojaat sarlavhasiga XOS EMAS — xuddi shu belgi donat
# bildirishnomasida (`donate.py`, "⭐ Yangi homiylik") va admin panelidagi
# foydalanuvchi kartasida ("🆔 DB: ...") ham ishlatiladi. Agar admin o'sha
# xabarlardan biriga reply qilsa (masalan donor'ga "rahmat" deb yozsa),
# `_ID_PATTERN` yolg'ondan mos kelib, xabar TASODIFIY boshqa foydalanuvchiga
# "rasmiy murojaat javobi" sifatida ketib qolishi mumkin edi. Shuning uchun
# faqat AYNAN shu ikki murojaat sarlavhasi (`_admin_view` dagi) mavjud
# bo'lgandagina ID qidiriladi.
_SUPPORT_HEADER_MARKERS = ("✉️ Yangi murojaat", "💬 Suhbat davomi")


def _extract_target_id(message: Message | None) -> int | None:
    """Reply qilingan xabardagi foydalanuvchi ID sini qaytaradi.

    Faqat murojaat sarlavhasiga (`_admin_view`) mos matnlarda qidiriladi —
    boshqa `🆔` ishlatadigan xabarlar (donat bildirishnomasi, admin
    paneli) bilan chalkashmasin.
    """
    if message is None:
        return None
    text = message.text or message.caption or ""
    if not any(marker in text for marker in _SUPPORT_HEADER_MARKERS):
        return None
    found = _ID_PATTERN.search(text)
    return int(found.group(1)) if found else None


# Admin paneli tugmalari (`bot/keyboards/admin.py`dagi barcha `text=...`
# qiymatlari). `admin_reply`dagi pin-fallback (`get_pinned_target`) UCHUN
# kerak: agar admin oldin "↩️ Javob yozish" tugmasini bosib UNUTGAN
# bo'lsa-yu, keyin panelda "📤 Reklama" kabi tugmani bossa, shu tugma
# matni tasodifan pin qilingan foydalanuvchiga "javob" sifatida ketib
# qolmasligi kerak. Yangi tugma qo'shilsa shu ro'yxatga ham qo'shing.
_ADMIN_PANEL_BUTTONS = frozenset(
    {
        "📜 Audit", "⛔ Bekor qilish", "✅ Blokdan chiqarish", "🚫 Bloklash",
        "🔁 Boshqa xabar", "▶️ Davom ettirish", "📨 Forward xabar yuborish",
        "👤 Foydalanuvchilar", "✅ Ha, baribir yubor", "🚀 Ha, hammaga yuborilsin",
        "🔧 Kanallar", "📋 Kanallar ro'yxati", "❌ Kanalni olib tashlash",
        "➕ Kanal qo'shish", "♻️ Limitni tozalash", "🔢 Limit o'rnatish",
        "✏️ Matn limiti", "📬 Oddiy xabar yuborish", "🔙 Orqaga",
        "✏️ Ovoz limiti", "🔊 Ovoz limiti", "🔇 Ovoz limitini tozalash",
        "⏸ Pauza", "🔍 Qidirish", "✏️ Rasm (bepul)", "🖼 Rasm limiti",
        "🚫 Rasm limitini tozalash", "✏️ Rasm (VIP)", "📤 Reklama",
        "📊 Statistika", "🗂 Tarix", "⚙️ Umumiy limitlar", "🔄 Yangilash",
        "✉️ Xabar yuborish",
    }
)


def _is_reserved(message: Message) -> bool:
    """Menyu tugmasi yoki buyruqmi.

    Bular murojaat matni sifatida qabul qilinmasligi kerak — foydalanuvchi
    "🌐 Tillar" bosganda u adminga ketib qolmasin.
    """
    text = message.text or ""
    return text.startswith("/") or text in RESERVED_BUTTONS or text in _ADMIN_PANEL_BUTTONS


@router.message(F.text.in_(CONTACT_BUTTONS))
@router.message(Command("contact"))
async def open_contact(
    message: Message,
    user: User,
    events: EventService,
    session_id,
    state: FSMContext,
    t: ModuleType,
) -> None:
    await events.log(
        EventType.MENU_OPENED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        menu="contact",
    )
    await state.set_state(SupportStates.waiting_message)
    await message.answer(t.CONTACT_PROMPT, reply_markup=cancel_menu(t))


@router.message(StateFilter(SupportStates.waiting_message), F.text.in_(CANCEL_BUTTONS))
async def cancel_contact(message: Message, state: FSMContext, t: ModuleType) -> None:
    await state.clear()
    await message.answer(t.CONTACT_CANCELLED, reply_markup=main_menu(t))


# Matn ham, media ham qabul qilinadi — filtr faqat menyu tugmalari va
# buyruqlarni chiqarib tashlaydi.
@router.message(
    StateFilter(SupportStates.waiting_message),
    lambda message: not _is_reserved(message),
)
async def receive_message(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    state: FSMContext,
    t: ModuleType,
    redis=None,
) -> None:
    described = _describe(message)
    text = (message.text or message.caption or "").strip()

    if len(text) > settings.SUPPORT_MAX_CHARS:
        await message.answer(
            t.CONTACT_TOO_LONG.format(
                length=len(text), limit=settings.SUPPORT_MAX_CHARS
            )
        )
        return

    minutes = await _rate_limited(redis, user.id)
    if minutes:
        await events.log(
            EventType.SUPPORT_RATE_LIMITED,
            user_id=user.id,
            chat_id=message.chat.id,
            session_id=session_id,
        )
        await state.clear()
        await message.answer(
            t.CONTACT_RATE_LIMITED.format(minutes=minutes), reply_markup=main_menu(t)
        )
        return

    # Avval yozib olamiz, keyin yuboramiz: Telegram yiqilsa ham matn qoladi.
    await events.log(
        EventType.SUPPORT_MESSAGE_SENT,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        text=truncate(described, settings.SUPPORT_MAX_CHARS),
        chars=len(text),
        username=user.username,
        telegram_id=user.telegram_id,
    )

    header = _admin_view(user)
    header_markup = support_reply_keyboard(user.id)
    support = SupportRepository(session)
    delivered = 0

    for admin_id in settings.ADMIN_USER_IDS:
        try:
            head_id, copy_id = await _deliver(message, admin_id, header, header_markup=header_markup)
            delivered += 1
            # Ikkala xabar ham ipga bog'lanadi — admin qaysi biriga reply
            # qilsa ham suhbat topilishi kerak.
            for admin_message_id in (head_id, copy_id):
                if admin_message_id is None:
                    continue
                await support.record(
                    user_id=user.id,
                    direction="in",
                    text=described,
                    admin_chat_id=admin_id,
                    admin_message_id=admin_message_id,
                    user_chat_id=message.chat.id,
                    user_message_id=message.message_id,
                )
        except Exception:
            # Bitta admin yetib olmasa qolganlariga yuborishda davom etamiz.
            logger.warning("Murojaatni %s ga yuborib bo'lmadi", admin_id, exc_info=True)

    await state.clear()

    if delivered:
        await message.answer(t.CONTACT_SENT, reply_markup=main_menu(t))
    else:
        # Voqea yozilgani uchun xabar yo'qolmadi, lekin foydalanuvchiga
        # "yuborildi" deb aytish yolg'on bo'lardi.
        logger.error("Murojaat hech bir adminga yetmadi (user_id=%s)", user.id)
        await message.answer(t.CONTACT_FAILED, reply_markup=main_menu(t))


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.ADMIN_USER_IDS


@router.callback_query(
    F.data.startswith("sup:pick:"),
    F.from_user.func(lambda u: u and _is_admin(u.id)),
)
async def pick_reply_target(callback: CallbackQuery, session: AsyncSession) -> None:
    """"↩️ Javob yozish" tugmasi — Telegram reply-havolasi ishlamay qolsa
    ham (izoh: `admin_reply`) ishlaydigan zaxira yo'l.

    Bosilgach shu suhbat "tanlangan" deb DB'ga yoziladi (FSM emas,
    muddatsiz) — keyingi oddiy xabar (reply bo'lmasa ham) o'shanga boradi.
    """
    try:
        target_user_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer("Xato.", show_alert=True)
        return

    support = SupportRepository(session)
    target = await support.find_user_by_id(target_user_id)
    if target is None:
        await callback.answer("Foydalanuvchi topilmadi.", show_alert=True)
        return

    await support.set_pinned_target(callback.from_user.id, target_user_id)
    name = target.first_name or (f"@{target.username}" if target.username else str(target.telegram_id))
    await callback.answer()
    await callback.message.answer(
        f"↩️ Endi <b>{html_escape(name)}</b>ga yozyapsiz — keyingi xabaringiz shunga yuboriladi."
    )


async def deliver_admin_message(
    message: Message,
    session: AsyncSession,
    events: EventService,
    session_id,
    target: User,
) -> tuple[bool, str]:
    """Adminning istalgan yo'ldan (reply, "↩️ Javob yozish" tugmasi, yoki
    admin panelidagi "✉️ Xabar yuborish") yozgan xabarini foydalanuvchiga
    yetkazadi va yozishma ipiga qo'shadi.

    BARCHA yo'llar shu FUNKSIYA orqali o'tadi — shu sababli qayerdan
    boshlangan bo'lishidan qat'i nazar, keyingi javoblar bir xilda
    (foydalanuvchi javob bersa "💬 Suhbat davomi" + "↩️ Javob yozish"
    tugmasi bilan) davom etadi.

    Qaytaradi: (muvaffaqiyat, adminga ko'rsatiladigan xabar).
    """
    support = SupportRepository(session)
    described = _describe(message)

    # Sarlavha foydalanuvchining o'z tilida.
    reply_locale = locales.get(
        target.settings.interface_lang if target.settings else None
    )

    try:
        head_id, copy_id = await _deliver(
            message, target.telegram_id, reply_locale.CONTACT_REPLY_HEADER
        )
    except Exception:
        logger.warning("Admin xabari yetmadi (user_id=%s)", target.id, exc_info=True)
        return False, "⚠️ Xabar yetkazilmadi — foydalanuvchi botni bloklagan bo'lishi mumkin."

    for user_message_id in (head_id, copy_id):
        if user_message_id is None:
            continue
        await support.record(
            user_id=target.id,
            direction="out",
            text=described,
            admin_chat_id=message.chat.id,
            admin_message_id=message.message_id,
            user_chat_id=target.telegram_id,
            user_message_id=user_message_id,
        )

    await events.log(
        EventType.SUPPORT_REPLY_SENT,
        user_id=target.id,
        chat_id=message.chat.id,
        session_id=session_id,
        chars=len(described),
    )
    return True, ("✅ Yuborildi." if copy_id else "⚠️ Faqat sarlavha ketdi — bu turni ko'chirib bo'lmadi.")


# FSM holati bo'yicha filtr ataylab YO'Q, garchi u tabiiy ko'rinsa ham.
#
# `StateFilter(None)` qo'yilganda quyidagi xavf tug'ilardi: admin xabar
# tarqatish rejimida turib murojaatga reply qilsa, bu handler ishlamay,
# xabar `panel` routeridagi tarqatish handleriga tushardi va bitta odamga
# mo'ljallangan javob 17 mingta foydalanuvchiga ketib qolardi.
#
# Ajratuvchi belgi — holat emas, **reply qilingan xabarning o'zi**: u
# `support_messages` da topilsa, admin aniq javob yozyapti. Topilmasa
# `SkipHandler` bilan odatdagi oqimga qaytaramiz.
#
# DIQQAT (2026-08-27 haqiqiy voqea): Telegram `reply_to_message`ni ESKI
# xabarlar uchun har doim ham UZATAVERMAYDI (hujjatlashtirilgan
# cheklovdan qat'i nazar, amalda bir necha soatdan keyin kuzatilgan) —
# shu holatda `F.reply_to_message` FILTRINING O'ZI mos kelmay qoladi,
# xabar to'g'ridan-to'g'ri `translate.py`ga tushib, oddiy tarjima
# sifatida ishlanib ketardi. Shuning uchun filtr ENDI reply talab
# qilmaydi — o'rniga, reply orqali topilmasa, "↩️ Javob yozish" tugmasi
# bilan TANLANGAN (pin qilingan) suhbatga tekshiriladi (pastda).
@router.message(F.from_user.func(lambda u: u and _is_admin(u.id)))
async def admin_reply(
    message: Message,
    session: AsyncSession,
    events: EventService,
    session_id,
    state: FSMContext,
) -> None:
    """Admin murojaat xabariga reply qilsa (yoki oldin "↩️ Javob yozish"
    tugmasini bosgan bo'lsa) — javob foydalanuvchiga boradi.

    Har qanday tur uzatiladi: matn, rasm, ovoz, hujjat.
    """
    support = SupportRepository(session)
    target = None

    if message.reply_to_message is not None:
        # 1. Sarlavhadagi ID — asosiy yo'l, eski xabarlarda ham ishlaydi
        #    (Telegram reply-havolasini uzatgan taqdirda).
        target_id = _extract_target_id(message.reply_to_message)
        if target_id is not None:
            target = await support.find_user(target_id)

        # 2. Zaxira: admin kontent nusxasiga reply qilgan bo'lsa sarlavha yo'q.
        if target is None:
            thread = await support.by_admin_message(
                message.chat.id, message.reply_to_message.message_id
            )
            target = thread.user if thread else None

    if target is None:
        # 3. Reply umuman yo'q (yoki topilmadi) — "↩️ Javob yozish" tugmasi
        # bilan tanlangan suhbatga tekshiramiz. FAQAT admin boshqa hech
        # qanday holatda (tarqatish, kanal qo'shish, limit kiritish va h.k.)
        # bo'lmasa va bu menyu tugmasi/buyruq bo'lmasa — aks holda admin
        # panelidagi matn kiritishlarni yoki o'zining oddiy tarjimalarini
        # ushlab qolgan bo'lardik.
        if await state.get_state() is None and not _is_reserved(message):
            target = await support.get_pinned_target(message.chat.id)

    if target is None:
        raise SkipHandler

    ok, reply_text = await deliver_admin_message(message, session, events, session_id, target)
    await message.reply(reply_text)
    if ok:
        # Adminning tugmasiz keyingi xabari ham shu foydalanuvchiga ketishi
        # uchun ("davom etadigan" suhbat) — bir martalik, izoh: `set_pinned_target`.
        await support.set_pinned_target(message.chat.id, target.id)


# Yuqoridagi kabi: ajratuvchi belgi reply qilingan xabar, holat emas.
# Holatga bog'liq handlerlar (murojaat yozish, homiylik miqdori) baribir
# oldinroq turadi va o'z navbatida ushlab qoladi.
@router.message(F.reply_to_message)
async def user_reply(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
    redis=None,
) -> None:
    """Foydalanuvchi admin javobiga reply qilsa — adminga qaytadi.

    Bu handler tarjima handleridan oldin turadi, aks holda javob matni
    tarjima qilinib yuborilardi. Ip topilmasa `SkipHandler` — oddiy reply
    bo'lsa matn odatdagidek tarjima qilinishi kerak.
    """
    support = SupportRepository(session)
    thread = await support.by_user_message(
        message.chat.id, message.reply_to_message.message_id
    )
    if thread is None:
        raise SkipHandler

    described = _describe(message)
    text = (message.text or message.caption or "").strip()

    if len(text) > settings.SUPPORT_MAX_CHARS:
        await message.answer(
            t.CONTACT_TOO_LONG.format(length=len(text), limit=settings.SUPPORT_MAX_CHARS)
        )
        return

    # Suhbat ichidagi javoblar uchun yumshoqroq cheklov: admin allaqachon
    # yozishmani boshlagan, uni soatiga 3 ta bilan cheklash mantiqsiz.
    minutes = await _rate_limited(
        redis, user.id, limit=settings.SUPPORT_REPLY_RATE_LIMIT, key="supportreply"
    )
    if minutes:
        await message.answer(t.CONTACT_RATE_LIMITED.format(minutes=minutes))
        return

    header = _admin_view(user, is_reply=True)
    header_markup = support_reply_keyboard(user.id)
    delivered = 0

    for admin_id in settings.ADMIN_USER_IDS:
        try:
            head_id, copy_id = await _deliver(message, admin_id, header, header_markup=header_markup)
            delivered += 1
            for admin_message_id in (head_id, copy_id):
                if admin_message_id is None:
                    continue
                await support.record(
                    user_id=user.id,
                    direction="in",
                    text=described,
                    admin_chat_id=admin_id,
                    admin_message_id=admin_message_id,
                    user_chat_id=message.chat.id,
                    user_message_id=message.message_id,
                )
        except Exception:
            logger.warning("Javobni %s ga yuborib bo'lmadi", admin_id, exc_info=True)

    await events.log(
        EventType.SUPPORT_MESSAGE_SENT,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        text=truncate(described, settings.SUPPORT_MAX_CHARS),
        chars=len(text),
        is_reply=True,
        username=user.username,
        telegram_id=user.telegram_id,
    )

    if delivered:
        await message.answer(t.CONTACT_REPLY_SENT)
    else:
        await message.answer(t.CONTACT_FAILED)
