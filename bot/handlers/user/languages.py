"""Tarjima yo'nalishini tanlash."""

from __future__ import annotations

from types import ModuleType
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User, UserSettings
from bot.database.repositories.language_repository import LanguageRepository
from bot.database.repositories.translation_repository import TranslationRepository
from bot.keyboards.user import (
    direction_label,
    lang_label,
    language_menu,
    language_picker,
    translation_actions,
)
from bot.locales import LANGUAGES_BUTTONS
from bot.services.events import EventService, EventType

router = Router(name="languages")


async def _menu_text_and_markup(
    session: AsyncSession, user_settings: UserSettings, t: ModuleType
):
    langs = LanguageRepository(session)
    source = await langs.by_code(user_settings.source_lang)
    target = await langs.by_code(user_settings.target_lang)
    text = t.LANGUAGE_MENU.format(
        source=lang_label(t, source), target=lang_label(t, target)
    )
    return text, language_menu(t, source, target)


@router.message(F.text.in_(LANGUAGES_BUTTONS))
@router.message(Command("lang"))
async def show_menu(
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
        menu="languages",
    )
    text, markup = await _menu_text_and_markup(session, user.settings, t)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "lang:menu")
async def back_to_menu(
    callback: CallbackQuery, session: AsyncSession, user: User, t: ModuleType
) -> None:
    text, markup = await _menu_text_and_markup(session, user.settings, t)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data.startswith("lang:pick:"))
async def pick_slot(
    callback: CallbackQuery, session: AsyncSession, user: User, t: ModuleType
) -> None:
    slot = callback.data.split(":")[2]
    langs = LanguageRepository(session)

    # `auto` faqat manba tili sifatida mantiqiy.
    options = await langs.selectable(include_auto=(slot == "source"))
    prompt = t.PICK_SOURCE if slot == "source" else t.PICK_TARGET

    await callback.message.edit_text(
        prompt, reply_markup=language_picker(t, options, slot)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lang:set:"))
async def set_language(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
) -> None:
    _, _, slot, code = callback.data.split(":", 3)

    langs = LanguageRepository(session)
    chosen = await langs.by_code(code)
    if chosen is None:
        await callback.answer(t.LANGUAGE_NOT_AVAILABLE, show_alert=True)
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
        t.LANG_SAVED.format(source=lang_label(t, source), target=lang_label(t, target)),
        reply_markup=language_menu(t, source, target),
    )
    await callback.answer("✅")


async def _apply_swap(
    session: AsyncSession,
    user: User,
    *,
    new_source: str,
    new_target: str,
    events: EventService,
    session_id,
) -> None:
    await session.execute(
        update(UserSettings)
        .where(UserSettings.user_id == user.id)
        .values(source_lang=new_source, target_lang=new_target)
    )
    user.settings.source_lang = new_source
    user.settings.target_lang = new_target

    await events.log(
        EventType.LANG_SWAPPED,
        user_id=user.id,
        session_id=session_id,
        source=new_source,
        target=new_target,
    )


@router.callback_query(F.data == "lang:swap")
async def swap_languages(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
) -> None:
    source_code = user.settings.source_lang
    target_code = user.settings.target_lang

    # `auto` ni maqsad til qilib bo'lmaydi — almashtirish mantiqsiz bo'lardi.
    if source_code == "auto":
        await callback.answer(t.SWAP_NEEDS_SOURCE, show_alert=True)
        return

    await _apply_swap(
        session,
        user,
        new_source=target_code,
        new_target=source_code,
        events=events,
        session_id=session_id,
    )

    langs = LanguageRepository(session)
    source = await langs.by_code(user.settings.source_lang)
    target = await langs.by_code(user.settings.target_lang)

    await callback.message.edit_text(
        t.LANG_SWAPPED.format(source=lang_label(t, source), target=lang_label(t, target)),
        reply_markup=language_menu(t, source, target),
    )
    await callback.answer("🔄")


# ─────────────────────────────────────────────────────────────
#  Tarjima ostidagi tugmalar
# ─────────────────────────────────────────────────────────────
async def _load_own_translation(
    session: AsyncSession, callback: CallbackQuery, user: User, t: ModuleType
) -> Optional[object]:
    try:
        translation_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer(t.TRANSLATION_NOT_FOUND, show_alert=True)
        return None

    translation = await TranslationRepository(session).get(translation_id)
    if translation is None or translation.user_id != user.id:
        await callback.answer(t.TRANSLATION_NOT_FOUND, show_alert=True)
        return None
    return translation


@router.callback_query(F.data.startswith("tr:swap:"))
async def swap_from_translation(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
) -> None:
    """Tarjima ostidagi almashtirish tugmasi.

    Natija tugmaning o'zida ko'rinadi: ikkinchi qatordagi yo'nalish tugmasi
    yangilanadi va ekranda qoladi. Bildirishnoma bir zumda o'chib ketadi,
    shuning uchun unga tayanmaymiz.
    """
    translation = await _load_own_translation(session, callback, user, t)
    if translation is None:
        return

    current_source = user.settings.source_lang
    current_target = user.settings.target_lang

    # Manba `auto` bo'lsa almashtirishning o'zi mantiqsiz bo'lardi, lekin
    # tarjima paytida haqiqiy til aniqlangan — uni yangi maqsad til qilamiz.
    # Ya'ni "auto → uz" tarjimadan keyin "uz → en" ga aylanadi.
    if current_source == "auto":
        detected = translation.source_lang_detected
        if not detected or detected == current_target:
            await callback.answer(t.SWAP_NEEDS_SOURCE, show_alert=True)
            return
        new_source, new_target = current_target, detected
    else:
        new_source, new_target = current_target, current_source

    await _apply_swap(
        session,
        user,
        new_source=new_source,
        new_target=new_target,
        events=events,
        session_id=session_id,
    )

    langs = LanguageRepository(session)
    source = await langs.by_code(new_source)
    target = await langs.by_code(new_target)

    voice = await langs.tts_voice(translation.target_lang)
    has_tts = bool(voice) and user.settings.tts_enabled

    try:
        await callback.message.edit_reply_markup(
            reply_markup=translation_actions(
                t, translation.id, has_tts=has_tts, source=source, target=target
            )
        )
    except Exception:
        # Bir xil klaviaturaga edit qilish Telegram xatosi beradi — zararsiz.
        pass

    await callback.answer(f"🔄 {direction_label(t, source, target)}")


@router.callback_query(F.data.startswith("tr:langs:"))
async def languages_from_translation(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
) -> None:
    """Tarjima ostidagi yo'nalish tugmasi — tillarni tanlash menyusini ochadi.

    Menyu yangi xabar bo'lib chiqadi: tarjima matni ekranda qolishi kerak,
    uni menyu bilan almashtirib bo'lmaydi.
    """
    await events.log(
        EventType.MENU_OPENED,
        user_id=user.id,
        chat_id=callback.message.chat.id if callback.message else None,
        session_id=session_id,
        menu="languages_from_translation",
    )

    text, markup = await _menu_text_and_markup(session, user.settings, t)
    await callback.message.answer(text, reply_markup=markup)
    await callback.answer()
