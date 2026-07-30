from __future__ import annotations

from types import ModuleType

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import User
from bot.database.repositories.language_repository import LanguageRepository
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
    t: ModuleType,
    is_new_user: bool = False,
    command: CommandObject | None = None,
) -> None:
    # /start dagi parametr — referal manbai. Faqat birinchi marta yoziladi.
    payload = (command.args or "").strip() if command else ""
    if is_new_user and payload:
        user.source = payload[:64]

    langs = LanguageRepository(session)
    source = await langs.by_code(user.settings.source_lang)
    target = await langs.by_code(user.settings.target_lang)

    await events.log(
        EventType.USER_START,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        is_new=is_new_user,
        source=payload or None,
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


@router.message(Command("help"))
async def cmd_help(
    message: Message, events: EventService, user: User, session_id, t: ModuleType
) -> None:
    await events.log(
        EventType.MENU_OPENED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        menu="help",
    )
    await message.answer(
        t.HELP.format(limit=settings.DAILY_TRANSLATION_LIMIT),
        reply_markup=main_menu(t),
    )
