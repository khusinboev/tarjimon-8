"""Sozlamalar: ovoz va interfeys tili."""

from __future__ import annotations

from types import ModuleType

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from bot import locales
from bot.config.settings import settings as config
from bot.database.models import User, UserSettings
from bot.database.repositories.usage_repository import UsageRepository
from bot.keyboards.user import interface_picker, main_menu, settings_menu
from bot.locales import SETTINGS_BUTTONS
from bot.services.events import EventService, EventType

router = Router(name="settings")

TOGGLES = {"tts_enabled"}


def _onoff(t: ModuleType, value: bool) -> str:
    return t.ON if value else t.OFF


async def _render(session: AsyncSession, user: User, t: ModuleType) -> tuple[str, object]:
    usage = await UsageRepository(session).get(user.id)
    used = usage.translations_count if usage else 0

    limit = user.settings.daily_limit_override or config.DAILY_TRANSLATION_LIMIT
    if user.role in ("admin", "owner"):
        limit = 0

    text = t.SETTINGS.format(
        tts=_onoff(t, user.settings.tts_enabled),
        interface=locales.get(user.settings.interface_lang).NAME,
        used=used,
        limit="∞" if limit <= 0 else limit,
    )
    markup = settings_menu(t, tts_enabled=user.settings.tts_enabled)
    return text, markup


@router.message(F.text.in_(SETTINGS_BUTTONS))
async def show_settings(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
) -> None:
    await events.log(
        EventType.MENU_OPENED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        menu="settings",
    )
    text, markup = await _render(session, user, t)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "set:menu")
async def back_to_settings(
    callback: CallbackQuery, session: AsyncSession, user: User, t: ModuleType
) -> None:
    text, markup = await _render(session, user, t)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "set:interface")
async def show_interface_picker(
    callback: CallbackQuery, user: User, t: ModuleType
) -> None:
    await callback.message.edit_text(
        t.PICK_INTERFACE,
        reply_markup=interface_picker(
            t, locales.names(), user.settings.interface_lang
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set:interface:"))
async def set_interface(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
) -> None:
    code = callback.data.split(":")[2]
    if code not in locales.SUPPORTED:
        await callback.answer(t.LANGUAGE_NOT_AVAILABLE, show_alert=True)
        return

    old_value = user.settings.interface_lang
    await session.execute(
        update(UserSettings)
        .where(UserSettings.user_id == user.id)
        .values(interface_lang=code)
    )
    user.settings.interface_lang = code

    await events.log(
        EventType.SETTINGS_CHANGED,
        user_id=user.id,
        chat_id=callback.message.chat.id if callback.message else None,
        session_id=session_id,
        field="interface_lang",
        old=old_value,
        new=code,
    )

    # Bundan keyingi hamma narsa yangi tilda — `t` ni shu yerda almashtiramiz,
    # aks holda javob eski tilda chiqardi.
    t = locales.get(code)

    await callback.message.edit_text(
        t.INTERFACE_SAVED.format(name=t.NAME),
        reply_markup=settings_menu(t, tts_enabled=user.settings.tts_enabled),
    )
    # Reply-menyu tugmalari ham yangi tilda bo'lishi kerak; ularni faqat yangi
    # xabar bilan almashtirish mumkin.
    await callback.message.answer(t.SEND_TEXT_PROMPT, reply_markup=main_menu(t))
    await callback.answer("✅")


@router.callback_query(F.data.startswith("set:toggle:"))
async def toggle(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
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

    await events.log(
        EventType.SETTINGS_CHANGED,
        user_id=user.id,
        chat_id=callback.message.chat.id if callback.message else None,
        session_id=session_id,
        field=field,
        old=old_value,
        new=new_value,
    )

    text, markup = await _render(session, user, t)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer("✅" if new_value else "❌")
