"""Sozlamalar: ovoz va maxfiylik."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings as config
from bot.database.models import User, UserSettings
from bot.database.repositories.usage_repository import UsageRepository
from bot.keyboards.user import BTN_SETTINGS, settings_menu
from bot.services.events import EventService, EventType
from bot.utils import texts

router = Router(name="settings")

TOGGLES = {"tts_enabled", "tts_auto", "save_history"}


async def _render(session: AsyncSession, user: User) -> tuple[str, object]:
    usage = await UsageRepository(session).get(user.id)
    used = usage.translations_count if usage else 0

    limit = user.settings.daily_limit_override or config.DAILY_TRANSLATION_LIMIT
    if user.role in ("admin", "owner"):
        limit = 0

    text = texts.SETTINGS.format(
        tts=texts.onoff(user.settings.tts_enabled),
        tts_auto=texts.onoff(user.settings.tts_auto),
        history=texts.onoff(user.settings.save_history),
        used=used,
        limit="∞" if limit <= 0 else limit,
    )
    markup = settings_menu(
        tts_enabled=user.settings.tts_enabled,
        tts_auto=user.settings.tts_auto,
        save_history=user.settings.save_history,
    )
    return text, markup


@router.message(F.text == BTN_SETTINGS)
async def show_settings(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
) -> None:
    await events.log(
        EventType.MENU_OPENED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        menu="settings",
    )
    text, markup = await _render(session, user)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("set:toggle:"))
async def toggle(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
) -> None:
    field = callback.data.split(":")[2]
    if field not in TOGGLES:
        await callback.answer()
        return

    old_value = getattr(user.settings, field)
    new_value = not old_value

    await session.execute(
        update(UserSettings).where(UserSettings.user_id == user.id).values(**{field: new_value})
    )
    setattr(user.settings, field, new_value)

    # Avto-ovoz ovoz tugmasisiz mantiqsiz — birgalikda o'chiriladi.
    if field == "tts_enabled" and not new_value and user.settings.tts_auto:
        await session.execute(
            update(UserSettings).where(UserSettings.user_id == user.id).values(tts_auto=False)
        )
        user.settings.tts_auto = False

    await events.log(
        EventType.SETTINGS_CHANGED,
        user_id=user.id,
        chat_id=callback.message.chat.id if callback.message else None,
        session_id=session_id,
        field=field,
        old=old_value,
        new=new_value,
    )

    text, markup = await _render(session, user)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer("✅" if new_value else "❌")
