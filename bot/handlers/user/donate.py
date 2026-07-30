"""Homiylik — Telegram Stars.

Telegram Stars (`XTR` valyutasi) uchun to'lov provayderi kerak emas:
`provider_token` bo'sh qoladi va Telegram to'lovni o'zi boshqaradi. Miqdor
butun son — Stars kasrga bo'linmaydi, ya'ni odatdagi "eng kichik birlik"
hisobi (tiyin/sent) bu yerda qo'llanmaydi.

Oqim: miqdor tanlanadi → invoice yuboriladi → `pre_checkout_query` ga
tasdiq beriladi → `successful_payment` kelganda yozib olinadi.

`pre_checkout_query` ga 10 soniya ichida javob berilmasa Telegram to'lovni
bekor qiladi, shuning uchun u yerda bazaga tegmaymiz — faqat tasdiq.
"""

from __future__ import annotations

import logging
from types import ModuleType

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import LabeledPrice, Message, PreCheckoutQuery
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import Donation, User
from bot.keyboards.user import cancel_menu, donate_amounts, main_menu
from bot.locales import CANCEL_BUTTONS, DONATE_BUTTONS, RESERVED_BUTTONS
from bot.services.events import EventService, EventType
from bot.states.support import DonateStates
from bot.utils.text import html_escape

logger = logging.getLogger(__name__)
router = Router(name="donate")

CURRENCY = "XTR"


async def _send_invoice(message: Message, t: ModuleType, stars: int) -> bool:
    """Invoice yuboradi. Muvaffaqiyat holatini qaytaradi."""
    try:
        await message.answer_invoice(
            title=t.DONATE_INVOICE_TITLE,
            description=t.DONATE_INVOICE_DESC.format(stars=stars),
            payload=f"donate:{stars}",
            currency=CURRENCY,
            # Stars uchun provayder tokeni yo'q — Telegram o'zi to'laydi.
            provider_token="",
            prices=[LabeledPrice(label=f"{stars} ⭐", amount=stars)],
        )
        return True
    except Exception:
        logger.exception("Invoice yuborib bo'lmadi (stars=%s)", stars)
        await message.answer(t.DONATE_FAILED, reply_markup=main_menu(t))
        return False


@router.message(F.text.in_(DONATE_BUTTONS))
@router.message(Command("donate"))
async def open_donate(
    message: Message,
    user: User,
    events: EventService,
    session_id,
    state: FSMContext,
    t: ModuleType,
) -> None:
    await state.clear()
    await events.log(
        EventType.DONATE_OPENED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
    )
    await message.answer(
        t.DONATE_INTRO,
        reply_markup=donate_amounts(
            t, settings.DONATE_PRESETS, with_cards=settings.HAS_DONATE_CARDS
        ),
    )


@router.callback_query(F.data == "donate:custom")
async def ask_custom_amount(callback, state: FSMContext, t: ModuleType) -> None:
    await state.set_state(DonateStates.waiting_custom_amount)
    await callback.message.answer(
        t.DONATE_CUSTOM_PROMPT.format(
            min=settings.DONATE_MIN, max=settings.DONATE_MAX
        ),
        reply_markup=cancel_menu(t),
    )
    await callback.answer()


@router.callback_query(F.data == "donate:cards")
async def show_cards(
    callback,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
) -> None:
    """Karta rekvizitlari. Raqamlar `<code>` ichida — bosib nusxa olinadi."""
    await events.log(
        EventType.DONATE_OPENED,
        user_id=user.id,
        chat_id=callback.message.chat.id if callback.message else None,
        session_id=session_id,
        method="card",
    )
    await callback.message.answer(
        t.DONATE_CARDS.format(
            visa=settings.DONATE_CARD_VISA,
            uzcard=settings.DONATE_CARD_UZCARD,
            holder=settings.DONATE_CARD_HOLDER,
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("donate:"))
async def pick_amount(
    callback,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
) -> None:
    raw = callback.data.split(":", 1)[1]
    if not raw.isdigit():
        await callback.answer()
        return

    stars = int(raw)
    if not settings.DONATE_MIN <= stars <= settings.DONATE_MAX:
        await callback.answer()
        return

    await callback.answer()
    if await _send_invoice(callback.message, t, stars):
        await events.log(
            EventType.DONATE_INVOICE_SENT,
            user_id=user.id,
            chat_id=callback.message.chat.id if callback.message else None,
            session_id=session_id,
            stars=stars,
            method="preset",
        )


@router.message(
    StateFilter(DonateStates.waiting_custom_amount), F.text.in_(CANCEL_BUTTONS)
)
async def cancel_custom(message: Message, state: FSMContext, t: ModuleType) -> None:
    await state.clear()
    await message.answer(t.CONTACT_CANCELLED, reply_markup=main_menu(t))


@router.message(
    StateFilter(DonateStates.waiting_custom_amount),
    F.text & ~F.text.startswith("/") & ~F.text.in_(RESERVED_BUTTONS),
)
async def receive_custom_amount(
    message: Message,
    user: User,
    events: EventService,
    session_id,
    state: FSMContext,
    t: ModuleType,
) -> None:
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit() or not (
        settings.DONATE_MIN <= int(raw) <= settings.DONATE_MAX
    ):
        await message.answer(
            t.DONATE_CUSTOM_INVALID.format(
                min=settings.DONATE_MIN, max=settings.DONATE_MAX
            )
        )
        return

    stars = int(raw)
    await state.clear()
    if await _send_invoice(message, t, stars):
        await events.log(
            EventType.DONATE_INVOICE_SENT,
            user_id=user.id,
            chat_id=message.chat.id,
            session_id=session_id,
            stars=stars,
            method="custom",
        )


@router.pre_checkout_query()
async def confirm_checkout(query: PreCheckoutQuery) -> None:
    """Telegram 10 soniya ichida javob kutadi — shu yerda bazaga tegmaymiz."""
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
) -> None:
    payment = message.successful_payment
    stars = payment.total_amount

    session.add(
        Donation(
            user_id=user.id,
            stars=stars,
            currency=payment.currency,
            telegram_payment_charge_id=payment.telegram_payment_charge_id,
            provider_payment_charge_id=payment.provider_payment_charge_id,
            invoice_payload=payment.invoice_payload,
            status="paid",
        )
    )

    try:
        await session.flush()
    except IntegrityError:
        # Telegram bir to'lov haqida takroriy xabar yuborishi mumkin —
        # `telegram_payment_charge_id` unikal, ikkinchi yozuv shu yerda
        # to'xtaydi. Foydalanuvchiga baribir rahmat aytamiz.
        await session.rollback()
        logger.info(
            "Takroriy to'lov xabari: %s", payment.telegram_payment_charge_id
        )
        await message.answer(
            t.DONATE_THANKS.format(stars=stars), reply_markup=main_menu(t)
        )
        return

    await events.log(
        EventType.DONATE_PAID,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        stars=stars,
        currency=payment.currency,
        charge_id=payment.telegram_payment_charge_id,
    )

    await message.answer(
        t.DONATE_THANKS.format(stars=stars), reply_markup=main_menu(t)
    )

    # Adminni xabardor qilamiz — homiylikni ko'rish va rahmat aytish uchun.
    username = f"@{user.username}" if user.username else "—"
    note = (
        "⭐ <b>Yangi homiylik</b>\n\n"
        f"👤 {html_escape(user.first_name or '—')} · {username}\n"
        f"🆔 <code>{user.telegram_id}</code>\n"
        f"💰 <b>{stars} ⭐</b>"
    )
    for admin_id in settings.ADMIN_USER_IDS:
        try:
            await message.bot.send_message(admin_id, note)
        except Exception:
            logger.warning("Homiylik xabarini %s ga yuborib bo'lmadi", admin_id)
