"""Adminga murojaat.

Bir tomonlama: foydalanuvchi yozadi, admin o'qiydi. Suhbat yo'q — admin
javob bermaydi va foydalanuvchi bilan yozishma boshlanmaydi.

Xabar ikki joyga tushadi:
  1. Adminlarning Telegram chatiga — darhol ko'rish uchun
  2. `events` jadvaliga (`support.message_sent`) — admin o'tkazib yuborsa
     yoki Telegram yuborishda xato bo'lsa matn yo'qolmasligi uchun

Ikkinchisi muhim: Telegram yuborish har xil sababdan yiqilishi mumkin
(admin botni bloklagan, chat topilmadi), lekin foydalanuvchi xabari
yo'qolmasligi kerak.
"""

from __future__ import annotations

import logging
from types import ModuleType

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config.settings import settings
from bot.database.models import User
from bot.keyboards.user import cancel_menu, main_menu
from bot.locales import CANCEL_BUTTONS, CONTACT_BUTTONS, RESERVED_BUTTONS
from bot.services.events import EventService, EventType
from bot.states.support import SupportStates
from bot.utils.text import html_escape, truncate

logger = logging.getLogger(__name__)
router = Router(name="support")


async def _rate_limited(redis, user_id: int) -> int:
    """Limit oshgan bo'lsa qolgan daqiqalarni qaytaradi, aks holda 0.

    Redis yo'q bo'lsa cheklov ishlamaydi (fail-open) — tarjima oqimidagi
    bilan bir xil qaror: Redis tushganda bot ishlashda davom etsin.
    """
    if not redis:
        return 0
    key = f"support:{user_id}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, settings.SUPPORT_RATE_WINDOW)
        if count > settings.SUPPORT_RATE_LIMIT:
            ttl = await redis.ttl(key)
            return max(1, (ttl + 59) // 60) if ttl and ttl > 0 else 1
        return 0
    except Exception:
        logger.warning("Murojaat limitini tekshirib bo'lmadi", exc_info=True)
        return 0


def _admin_view(user: User, text: str) -> str:
    """Adminga ko'rinadigan ko'rinish.

    Admin — bitta odam (egasi), shuning uchun bu matn tarjima qilinmaydi.
    `html_escape` shart: foydalanuvchi matnida `<` bo'lsa xabar yuborilmaydi.
    """
    username = f"@{user.username}" if user.username else "—"
    name = html_escape(user.first_name or "—")
    return (
        "✉️ <b>Yangi murojaat</b>\n\n"
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
    delivered = 0
    for admin_id in settings.ADMIN_USER_IDS:
        try:
            await message.bot.send_message(admin_id, body)
            delivered += 1
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


@router.message(StateFilter(SupportStates.waiting_message))
async def reject_non_text(message: Message, t: ModuleType) -> None:
    """Matn bo'lmagan hamma narsa — rasm, ovoz, stiker.

    Holat saqlanadi: foydalanuvchi matn ko'rinishida qayta yuborishi mumkin.
    """
    await message.answer(t.CONTACT_ONLY_TEXT)
