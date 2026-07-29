from __future__ import annotations

from aiogram import F, Router
from aiogram.types import Message

from bot.config.settings import settings
from bot.database.models import User
from bot.keyboards.user import BTN_HELP, main_menu
from bot.services.events import EventService, EventType
from bot.utils import texts

router = Router(name="common")


@router.message(F.text == BTN_HELP)
async def show_help(
    message: Message, user: User, events: EventService, session_id
) -> None:
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
