from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import User
from bot.database.repositories.language_repository import LanguageRepository
from bot.keyboards.user import main_menu
from bot.services.events import EventService, EventType
from bot.utils import texts

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
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

    template = texts.WELCOME if is_new_user else texts.WELCOME_BACK
    await message.answer(
        template.format(
            name=message.from_user.first_name or "do'stim",
            source=f"{source.flag} {source.name_uz}" if source else user.settings.source_lang,
            target=f"{target.flag} {target.name_uz}" if target else user.settings.target_lang,
        ),
        reply_markup=main_menu(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message, events: EventService, user: User, session_id) -> None:
    await events.log(
        EventType.MENU_OPENED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        menu="help",
    )
    await message.answer(
        texts.HELP.format(limit=settings.DAILY_TRANSLATION_LIMIT),
        reply_markup=main_menu(),
    )
