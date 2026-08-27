"""Adminga murojaat va yozishma.

**2026-08-27 — to'liq qayta qurildi: FAQAT ANIQ TUGMA orqali, reply
mexanizmiga UMUMAN tayanmaydi.** Ilgari suhbat Telegram'ning "reply"
xususiyati orqali borardi (admin/user bir-birining xabariga reply qilsa
javob topilardi) — lekin Telegram `message.reply_to_message`ni ESKI
xabarlar uchun har doim ham uzatavermaydi (hujjatlashtirilgan
cheklovdan qat'i nazar, amalda bir necha soatdan keyin kuzatilgan
haqiqiy voqea), ya'ni reply-ga tayanish TABIATAN ishonchsiz.

Yangi tartib — ikkala tomon uchun ham BIR XIL mantiq: har bir xabar
FAQAT ANIQ TUGMA bosilgach yuboriladi, boshqa hech qanday holatda emas:

  foydalanuvchi tomoni:
    "✉️ Adminga murojaat" (menyu) YOKI oldingi "yuborildi" xabaridagi
    "🔁 Yana yuborish" tugmasi → `SupportStates.waiting_message` holati
    → keyingi xabar adminga ketadi → tasdiq + YANGI "🔁 Yana yuborish"
    tugmasi. Tugmasiz oddiy xabar hech qachon adminga ketmaydi.

  admin tomoni:
    murojaat sarlavhasidagi "↩️ Javob yozish" tugmasi → shu suhbat
    `admin_reply_targets`da (FSM EMAS, oddiy DB qatori) "tanlanadi" →
    keyingi xabar shu foydalanuvchiga ketadi (bir martalik — yuborilgach
    darhol o'chadi, yana yozish uchun tugma QAYTA bosilishi kerak).
    Tugmasiz oddiy xabar (yoki reply) hech qachon foydalanuvchiga
    ketmaydi — faqat pin orqali.

Har qanday tur uzatiladi — rasm, video, ovoz, hujjat, stiker. Buning uchun
`copy_message` ishlatiladi: media qayta yuklanmaydi, Telegram faylni o'zida
ko'chiradi. Har bir uzatishda ikki xabar ketadi (sarlavha + kontent) —
faqat TARIX/audit uchun `support_messages`ga yoziladi, suhbatni
ANIQLASH uchun ENDI ishlatilmaydi (buni tugma/pin qiladi).

Murojaat matni ikki joyga tushadi:
  1. Adminlarning Telegram chatiga — darhol ko'rish uchun
  2. `events` jadvaliga (`support.message_sent`) — admin o'tkazib yuborsa
     yoki Telegram yuborishda xato bo'lsa matn yo'qolmasligi uchun
"""

from __future__ import annotations

import logging
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
from bot.keyboards.inline import contact_send_again_keyboard, support_reply_keyboard
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


def _admin_view(user: User) -> str:
    """Adminga ko'rinadigan sarlavha.

    Admin — bitta odam (egasi), shuning uchun bu matn tarjima qilinmaydi.
    Kontentning o'zi alohida xabar bo'lib keladi (`_deliver`), bu yerda
    faqat kim yozgani ko'rsatiladi. Bitta ko'rinish — "birinchi" va
    "davomi" farqi endi yo'q, chunki HAR bir xabar (birinchi bo'ladimi,
    "🔁 Yana yuborish" orqali keyingisimi) bir xil yo'l bilan, ANIQ tugma
    bosilgach keladi.
    """
    username = f"@{user.username}" if user.username else "—"
    name = html_escape(user.first_name or "—")
    return (
        "✉️ <b>Murojaat</b>\n\n"
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
        # Inline "🔁 Yana yuborish" — reply-ga UMUMAN tayanmasdan yana
        # xabar yuborishning YAGONA yo'li. Telegram bitta xabarga faqat
        # bitta klaviatura turi biriktirishga ruxsat beradi (pastdagi
        # doimiy tugmalar EMAS, inline), shuning uchun `main_menu(t)`
        # bu yerda qo'llanmaydi — foydalanuvchining pastdagi menyusi
        # keyingi navigatsiyada o'zi tiklanadi.
        await message.answer(t.CONTACT_SENT, reply_markup=contact_send_again_keyboard(t))
    else:
        # Voqea yozilgani uchun xabar yo'qolmadi, lekin foydalanuvchiga
        # "yuborildi" deb aytish yolg'on bo'lardi.
        logger.error("Murojaat hech bir adminga yetmadi (user_id=%s)", user.id)
        await message.answer(t.CONTACT_FAILED, reply_markup=main_menu(t))


@router.callback_query(F.data == "sup:again")
async def send_again(
    callback: CallbackQuery,
    user: User,
    events: EventService,
    session_id,
    state: FSMContext,
    t: ModuleType,
) -> None:
    """"🔁 Yana yuborish" — `open_contact` bilan bir xil, faqat menyudan
    emas, oldingi "yuborildi" xabaridan ishga tushadi. Bu tugma ESKI
    xabarlarda ham abadiy ishlaydi — reply-havolasi kabi vaqt bilan
    yo'qolmaydi, shuning uchun murojaatni istalgan payt davom ettirish
    mumkin.
    """
    await callback.answer()
    await events.log(
        EventType.MENU_OPENED,
        user_id=user.id,
        chat_id=callback.message.chat.id if callback.message else None,
        session_id=session_id,
        menu="contact_again",
    )
    await state.set_state(SupportStates.waiting_message)
    if callback.message:
        await callback.message.answer(t.CONTACT_PROMPT, reply_markup=cancel_menu(t))


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
    """Adminning "↩️ Javob yozish" tugmasi orqali (reply, yoki admin
    panelidagi "✉️ Xabar yuborish") yozgan xabarini foydalanuvchiga
    yetkazadi va tarix uchun yozib qo'yadi.

    BARCHA yo'llar shu FUNKSIYA orqali o'tadi — shu sababli qayerdan
    boshlangan bo'lishidan qat'i nazar, natija bir xil ko'rinadi.

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
            message,
            target.telegram_id,
            reply_locale.CONTACT_REPLY_HEADER,
            header_markup=contact_send_again_keyboard(reply_locale),
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
# tarqatish rejimida turib "↩️ Javob yozish"ni bosgandan keyin yozsa, bu
# handler ishlamay, xabar `panel` routeridagi tarqatish handleriga
# tushardi va bitta odamga mo'ljallangan javob 17 mingta foydalanuvchiga
# ketib qolardi. Shuning uchun himoya pastda `state.get_state() is None`
# sharti bilan qo'lda qo'shilgan.
#
# Ajratuvchi belgi — reply HAM, `support_messages` HAM EMAS (2026-08-27
# to'liq qayta qurilgach): FAQAT `admin_reply_targets`dagi "pin" — ya'ni
# admin oldin "↩️ Javob yozish" tugmasini bosganmi. Bosmagan bo'lsa —
# xabar hech qachon foydalanuvchiga ketmaydi, oddiy oqimga (`SkipHandler`)
# qaytadi.
@router.message(F.from_user.func(lambda u: u and _is_admin(u.id)))
async def admin_reply(
    message: Message,
    session: AsyncSession,
    events: EventService,
    session_id,
    state: FSMContext,
) -> None:
    """Admin oldin "↩️ Javob yozish" tugmasini bosgan bo'lsa — keyingi
    xabari o'sha foydalanuvchiga boradi. BIR MARTALIK: yuborilgach pin
    darhol o'chadi, yana yozish uchun tugma QAYTA bosilishi kerak
    (`qolgan hollarda xabar yuborolmaydi` — ataylab shunday, tasodifan
    boshqa foydalanuvchiga ketib qolmasin deb).

    Har qanday tur uzatiladi: matn, rasm, ovoz, hujjat.
    """
    support = SupportRepository(session)
    target = None

    # FAQAT admin boshqa hech qanday holatda (tarqatish, kanal qo'shish,
    # limit kiritish va h.k.) bo'lmasa va bu menyu tugmasi/buyruq bo'lmasa
    # — aks holda admin panelidagi matn kiritishlarni yoki o'zining oddiy
    # tarjimalarini ushlab qolgan bo'lardik.
    if await state.get_state() is None and not _is_reserved(message):
        target = await support.get_pinned_target(message.chat.id)

    if target is None:
        raise SkipHandler

    ok, reply_text = await deliver_admin_message(message, session, events, session_id, target)
    await message.reply(reply_text)
