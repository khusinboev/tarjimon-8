"""Tarjima yo'nalishini tanlash."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User, UserSettings
from bot.database.repositories.language_repository import LanguageRepository
from bot.keyboards.user import BTN_LANGUAGES, language_menu, language_picker
from bot.services.events import EventService, EventType
from bot.utils import texts

router = Router(name="languages")


def label(lang) -> str:
    return f"{lang.flag} {lang.name_uz}" if lang else "—"


async def _menu_text_and_markup(session: AsyncSession, user_settings: UserSettings):
    langs = LanguageRepository(session)
    source = await langs.by_code(user_settings.source_lang)
    target = await langs.by_code(user_settings.target_lang)
    text = texts.LANGUAGE_MENU.format(source=label(source), target=label(target))
    return text, language_menu(source, target)


@router.message(F.text == BTN_LANGUAGES)
@router.message(Command("lang"))
async def show_menu(
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
        menu="languages",
    )
    text, markup = await _menu_text_and_markup(session, user.settings)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "lang:menu")
async def back_to_menu(
    callback: CallbackQuery, session: AsyncSession, user: User
) -> None:
    text, markup = await _menu_text_and_markup(session, user.settings)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("lang:pick:"))
async def pick_slot(
    callback: CallbackQuery, session: AsyncSession, user: User
) -> None:
    slot = callback.data.split(":")[2]
    langs = LanguageRepository(session)

    # `auto` faqat manba tili sifatida mantiqiy.
    options = await langs.selectable(include_auto=(slot == "source"))
    prompt = texts.PICK_SOURCE if slot == "source" else texts.PICK_TARGET

    await callback.message.edit_text(prompt, reply_markup=language_picker(options, slot))
    await callback.answer()


@router.callback_query(F.data.startswith("lang:set:"))
async def set_language(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
) -> None:
    _, _, slot, code = callback.data.split(":", 3)

    langs = LanguageRepository(session)
    chosen = await langs.by_code(code)
    if chosen is None:
        await callback.answer("Bu til mavjud emas", show_alert=True)
        return

    field = "source_lang" if slot == "source" else "target_lang"
    old_value = getattr(user.settings, field)

    await session.execute(
        update(UserSettings).where(UserSettings.user_id == user.id).values(**{field: code})
    )
    setattr(user.settings, field, code)

    await events.log(
        EventType.LANG_SELECTED,
        user_id=user.id,
        chat_id=callback.message.chat.id if callback.message else None,
        session_id=session_id,
        slot=slot,
        code=code,
        old=old_value,
        method="menu",
    )

    source = await langs.by_code(user.settings.source_lang)
    target = await langs.by_code(user.settings.target_lang)

    await callback.message.edit_text(
        texts.LANG_SAVED.format(source=label(source), target=label(target)),
        reply_markup=language_menu(source, target),
    )
    await callback.answer("✅")


@router.callback_query(F.data == "lang:swap")
async def swap_languages(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
) -> None:
    source_code = user.settings.source_lang
    target_code = user.settings.target_lang

    # `auto` ni maqsad til qilib bo'lmaydi — almashtirish mantiqsiz bo'lardi.
    if source_code == "auto":
        await callback.answer(
            "Avto aniqlashni maqsad til qilib bo'lmaydi. Avval manba tilni tanlang.",
            show_alert=True,
        )
        return

    await session.execute(
        update(UserSettings)
        .where(UserSettings.user_id == user.id)
        .values(source_lang=target_code, target_lang=source_code)
    )
    user.settings.source_lang, user.settings.target_lang = target_code, source_code

    await events.log(
        EventType.LANG_SWAPPED,
        user_id=user.id,
        chat_id=callback.message.chat.id if callback.message else None,
        session_id=session_id,
        source=target_code,
        target=source_code,
    )

    langs = LanguageRepository(session)
    source = await langs.by_code(user.settings.source_lang)
    target = await langs.by_code(user.settings.target_lang)

    await callback.message.edit_text(
        texts.LANG_SWAPPED.format(source=label(source), target=label(target)),
        reply_markup=language_menu(source, target),
    )
    await callback.answer("🔄")
