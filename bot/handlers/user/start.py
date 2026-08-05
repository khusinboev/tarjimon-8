from __future__ import annotations

from types import ModuleType

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import User
from bot.database.repositories.language_repository import LanguageRepository
from bot.database.repositories.user_repository import UserRepository
from bot.keyboards.user import lang_label, main_menu
from bot.services.events import EventService, EventType

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    state: FSMContext,
    t: ModuleType,
    is_new_user: bool = False,
    command: CommandObject | None = None,
) -> None:
    await state.clear()
    # /start dagi parametr — referal manbai. Faqat birinchi marta yoziladi.
    payload = (command.args or "").strip() if command else ""
    if is_new_user and payload:
        user.source = payload[:64]

        # Payload raqam bo'lsa — bu taklif qilgan odamning Telegram ID'si
        # (`/invite` shunday havola beradi). Bonus BU YERDA berilmaydi —
        # faqat yangi user birinchi marta muvaffaqiyatli tarjima qilganda
        # (`bot/services/referral.py`), aks holda havolani ochib-yopib
        # (haqiqiy foydalanmasdan) mukofot yig'ish oson bo'lardi.
        if payload.isdigit():
            referrer_tg_id = int(payload)
            if referrer_tg_id != user.telegram_id:
                referrer = await UserRepository(session).get_by_telegram_id(
                    referrer_tg_id
                )
                if referrer is not None and referrer.id != user.id:
                    user.referred_by = referrer.id
                    await events.log(
                        EventType.USER_REFERRED,
                        user_id=user.id,
                        chat_id=message.chat.id,
                        session_id=session_id,
                        referrer_id=referrer.id,
                    )

    langs = LanguageRepository(session)
    source = await langs.by_code(user.settings.source_lang)
    target = await langs.by_code(user.settings.target_lang)

    await events.log(
        EventType.USER_START,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        is_new=is_new_user,
        start_param=payload or None,
    )

    template = t.WELCOME if is_new_user else t.WELCOME_BACK
    await message.answer(
        template.format(
            name=message.from_user.first_name or "friend",
            source=lang_label(t, source),
            target=lang_label(t, target),
        ),
        reply_markup=main_menu(t),
    )


@router.message(Command("invite"))
async def cmd_invite(
    message: Message,
    events: EventService,
    user: User,
    session_id,
    state: FSMContext,
    t: ModuleType,
) -> None:
    """Shaxsiy referal havolasi.

    Payload — foydalanuvchining Telegram ID'si (raqam bo'lgani uchun
    kodlash shart emas, Telegram deep-link cheklovlariga bemalol sig'adi).
    Havola orqali kirgan yangi user birinchi tarjimasidan keyin ikkalangizga
    ham VIP kun beriladi (`bot/services/referral.py`).
    """
    await state.clear()
    await events.log(
        EventType.MENU_OPENED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        menu="invite",
    )

    me = await message.bot.get_me()
    link = f"https://t.me/{me.username}?start={user.telegram_id}"
    await message.answer(
        t.INVITE_TEXT.format(
            link=link,
            referrer_days=settings.REFERRAL_BONUS_DAYS,
            welcome_days=settings.REFERRAL_WELCOME_DAYS,
        ),
        reply_markup=main_menu(t),
    )


@router.message(Command("help"))
async def cmd_help(
    message: Message,
    events: EventService,
    user: User,
    session_id,
    state: FSMContext,
    t: ModuleType,
) -> None:
    await state.clear()
    await events.log(
        EventType.MENU_OPENED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        menu="help",
    )
    await message.answer(
        t.HELP.format(
            limit=settings.DAILY_TRANSLATION_LIMIT,
            admin=settings.ADMIN_USERNAME,
        ),
        reply_markup=main_menu(t),
    )
