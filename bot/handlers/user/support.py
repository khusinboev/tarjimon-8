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
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import User
from bot.database.repositories.support_repository import SupportRepository
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
        count = await redis.incr(redis_key)
        if count == 1:
            await redis.expire(redis_key, settings.SUPPORT_RATE_WINDOW)
        if count > cap:
            ttl = await redis.ttl(redis_key)
            return max(1, (ttl + 59) // 60) if ttl and ttl > 0 else 1
        return 0
    except Exception:
        logger.warning("Murojaat limitini tekshirib bo'lmadi", exc_info=True)
        return 0


def _admin_view(user: User, text: str, *, is_reply: bool = False) -> str:
    """Adminga ko'rinadigan ko'rinish.

    Admin — bitta odam (egasi), shuning uchun bu matn tarjima qilinmaydi.
    `html_escape` shart: foydalanuvchi matnida `<` bo'lsa xabar yuborilmaydi.
    """
    username = f"@{user.username}" if user.username else "—"
    name = html_escape(user.first_name or "—")
    title = "💬 <b>Suhbat davomi</b>" if is_reply else "✉️ <b>Yangi murojaat</b>"
    return (
        f"{title}\n\n"
        f"👤 {name} · {username}\n"
        f"🆔 <code>{user.telegram_id}</code>\n"
        f"🌐 {user.telegram_lang or '—'}\n"
        "──────────\n\n"
        f"{html_escape(text)}"
    )


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


@router.message(
    StateFilter(SupportStates.waiting_message),
    F.text & ~F.text.startswith("/") & ~F.text.in_(RESERVED_BUTTONS),
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
    text = (message.text or "").strip()
    if not text:
        return

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
        text=truncate(text, settings.SUPPORT_MAX_CHARS),
        chars=len(text),
        username=user.username,
        telegram_id=user.telegram_id,
    )

    body = _admin_view(user, text)
    support = SupportRepository(session)
    delivered = 0
    for admin_id in settings.ADMIN_USER_IDS:
        try:
            sent = await message.bot.send_message(admin_id, body)
            delivered += 1
            # Ipni yozamiz: admin shu xabarga reply qilsa kimga javob
            # berayotganini shundan topamiz.
            await support.record(
                user_id=user.id,
                direction="in",
                text=text,
                admin_chat_id=admin_id,
                admin_message_id=sent.message_id,
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


# Buyruqlar bu yerga tushmasligi kerak: murojaat yozayotgan odam `/donate`
# yozsa unga "matn yuboring" deb javob berish noto'g'ri bo'lardi. Buyruq
# routerlari `support` dan oldin turadi, lekin filtrda ham aniq yozamiz —
# keyinchalik yangi buyruq qo'shilib, tartibi keyinroq qolib ketishi mumkin.
@router.message(StateFilter(SupportStates.waiting_message), ~F.text.startswith("/"))
async def reject_non_text(message: Message, t: ModuleType) -> None:
    """Matn bo'lmagan hamma narsa — rasm, ovoz, stiker.

    Holat saqlanadi: foydalanuvchi matn ko'rinishida qayta yuborishi mumkin.
    """
    await message.answer(t.CONTACT_ONLY_TEXT)


def _is_admin(telegram_id: int) -> bool:
    return telegram_id in settings.ADMIN_USER_IDS


# `StateFilter(None)` shart: admin xabar tarqatish rejimida turib eski
# murojaatga reply qilsa, matn tarqatish o'rniga bitta userga ketib qolardi.
@router.message(
    StateFilter(None),
    F.reply_to_message,
    F.text,
    F.from_user.func(lambda u: u and _is_admin(u.id)),
)
async def admin_reply(
    message: Message,
    session: AsyncSession,
    events: EventService,
    session_id,
) -> None:
    """Admin murojaat xabariga reply qilsa — javob foydalanuvchiga boradi.

    Ip `support_messages` dan topiladi: admin qaysi xabarga javob berayotgan
    bo'lsa, o'sha yozuvdagi foydalanuvchi nishon bo'ladi. Boshqa xabarga
    reply bo'lsa `SkipHandler` bilan keyingi handlerlarga o'tkazamiz —
    admin ham botdan tarjima uchun foydalanishi mumkin.
    """
    support = SupportRepository(session)
    thread = await support.by_admin_message(
        message.chat.id, message.reply_to_message.message_id
    )
    if thread is None or thread.user is None:
        raise SkipHandler

    text = (message.text or "").strip()
    if not text:
        raise SkipHandler

    target = thread.user
    # Javob foydalanuvchining o'z tilida sarlavhalanadi.
    reply_locale = locales.get(target.settings.interface_lang if target.settings else None)

    try:
        sent = await message.bot.send_message(
            target.telegram_id,
            reply_locale.CONTACT_REPLY_HEADER.format(text=html_escape(text)),
        )
    except Exception:
        logger.warning("Admin javobi yetmadi (user_id=%s)", target.id, exc_info=True)
        await message.reply("⚠️ Javob yetkazilmadi — foydalanuvchi botni bloklagan bo'lishi mumkin.")
        return

    await support.record(
        user_id=target.id,
        direction="out",
        text=text,
        admin_chat_id=message.chat.id,
        admin_message_id=message.message_id,
        user_chat_id=target.telegram_id,
        user_message_id=sent.message_id,
    )
    await events.log(
        EventType.SUPPORT_REPLY_SENT,
        user_id=target.id,
        chat_id=message.chat.id,
        session_id=session_id,
        chars=len(text),
    )
    await message.reply("✅ Yuborildi.")


@router.message(StateFilter(None), F.reply_to_message, F.text)
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

    text = (message.text or "").strip()
    if not text:
        raise SkipHandler

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

    body = _admin_view(user, text, is_reply=True)
    delivered = 0
    for admin_id in settings.ADMIN_USER_IDS:
        try:
            sent = await message.bot.send_message(admin_id, body)
            delivered += 1
            await support.record(
                user_id=user.id,
                direction="in",
                text=text,
                admin_chat_id=admin_id,
                admin_message_id=sent.message_id,
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
        text=truncate(text, settings.SUPPORT_MAX_CHARS),
        chars=len(text),
        is_reply=True,
        username=user.username,
        telegram_id=user.telegram_id,
    )

    if delivered:
        await message.answer(t.CONTACT_REPLY_SENT)
    else:
        await message.answer(t.CONTACT_FAILED)
