from __future__ import annotations

from types import ModuleType

from aiogram import F, Router
from aiogram.types import Message

from bot.config.settings import settings
from bot.database.models import User
from bot.keyboards.user import main_menu
from bot.locales import HELP_BUTTONS
from bot.services.events import EventService, EventType

router = Router(name="common")


@router.message(F.text.in_(HELP_BUTTONS))
async def show_help(
    message: Message, user: User, events: EventService, session_id, t: ModuleType
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
