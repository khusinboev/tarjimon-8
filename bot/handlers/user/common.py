from __future__ import annotations

from types import ModuleType

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.config.settings import settings
from bot.database.models import User
from bot.keyboards.user import main_menu
from bot.locales import HELP_BUTTONS
from bot.services.events import EventService, EventType

router = Router(name="common")


@router.message(F.text.in_(HELP_BUTTONS))
async def show_help(
    message: Message,
    user: User,
    events: EventService,
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


@router.message(Command("developer"))
async def show_developer(message: Message, t: ModuleType) -> None:
    """Dasturchi kontakti.

    Ommaviy buyruq — shuning uchun foydalanuvchi tilida chiqadi va username
    `settings` dan olinadi. Ilgari admin panelida qattiq yozilgan begona
    username turgan edi.
    """
    await message.answer(t.DEVELOPER.format(admin=settings.ADMIN_USERNAME))
