"""Tarix va sevimlilar."""

from __future__ import annotations

import math

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.database.repositories.language_repository import LanguageRepository
from bot.database.repositories.translation_repository import TranslationRepository
from bot.keyboards.user import BTN_HISTORY, history_nav, translation_actions
from bot.services.events import EventService, EventType
from bot.utils import texts
from bot.utils.text import truncate

router = Router(name="history")

PAGE_SIZE = 5
PREVIEW_CHARS = 220


async def _render_page(
    session: AsyncSession, user: User, *, mode: str, page: int
) -> tuple[str, object] | None:
    repo = TranslationRepository(session)
    langs = await LanguageRepository(session).as_dict()

    if mode == "favorites":
        items = await repo.favorites(user.id, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
        # Sevimlilar soni signal jadvalidan hisoblanadi; sahifalash uchun
        # bir sahifa oldinga qarab tekshiramiz — alohida COUNT so'roviga arzimaydi.
        has_next = bool(
            await repo.favorites(user.id, limit=1, offset=page * PAGE_SIZE)
        )
        pages = page + 1 if has_next else page
        header = texts.FAVORITES_HEADER
        empty = texts.FAVORITES_EMPTY
    else:
        items = await repo.history(user.id, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE)
        total = await repo.history_count(user.id)
        pages = max(1, math.ceil(total / PAGE_SIZE))
        header = texts.HISTORY_HEADER
        empty = texts.HISTORY_EMPTY

    if not items:
        return (empty, None) if page == 1 else None

    lines = [header.format(page=page, pages=pages), ""]
    for item in items:
        src = langs.get(item.source_lang_detected or item.source_lang_requested)
        dst = langs.get(item.target_lang)
        arrow = f"{src.flag if src else '🌐'} → {dst.flag if dst else '🌐'}"
        lines.append(f"{arrow}  <i>{item.created_at:%d.%m %H:%M}</i>")
        lines.append(f"<b>{truncate(item.source_text, PREVIEW_CHARS)}</b>")
        lines.append(truncate(item.target_text or "", PREVIEW_CHARS))
        lines.append("")

    return "\n".join(lines), history_nav(page, pages, mode=mode)


@router.message(F.text == BTN_HISTORY)
async def show_history(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
) -> None:
    await events.log(
        EventType.HISTORY_VIEWED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        page=1,
        mode="history",
    )

    if not user.settings.save_history:
        await message.answer(texts.HISTORY_DISABLED)
        return

    rendered = await _render_page(session, user, mode="history", page=1)
    if rendered is None:
        await message.answer(texts.HISTORY_EMPTY)
        return

    text, markup = rendered
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("hist:"))
async def paginate(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
) -> None:
    try:
        _, mode, raw_page = callback.data.split(":")
        page = max(1, int(raw_page))
    except ValueError:
        await callback.answer()
        return

    await events.log(
        EventType.HISTORY_VIEWED,
        user_id=user.id,
        chat_id=callback.message.chat.id if callback.message else None,
        session_id=session_id,
        page=page,
        mode=mode,
    )

    rendered = await _render_page(session, user, mode=mode, page=page)
    if rendered is None:
        await callback.answer("Boshqa yozuv yo'q")
        return

    text, markup = rendered
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except Exception:
        # Bir xil matnga edit qilishga urinish Telegram xatosi beradi — zararsiz.
        pass
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("tr:fav:"))
async def toggle_favorite(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
) -> None:
    try:
        translation_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer(texts.TRANSLATION_NOT_FOUND, show_alert=True)
        return

    repo = TranslationRepository(session)
    translation = await repo.get(translation_id)
    if translation is None or translation.user_id != user.id:
        await callback.answer(texts.TRANSLATION_NOT_FOUND, show_alert=True)
        return

    currently = await repo.is_favorite(user.id, translation_id)
    signal = "unfavorited" if currently else "favorited"

    await repo.add_signal(translation_id=translation_id, user_id=user.id, signal=signal)
    await events.log(
        EventType.FEEDBACK_GIVEN,
        user_id=user.id,
        chat_id=callback.message.chat.id if callback.message else None,
        session_id=session_id,
        translation_id=translation_id,
        signal=signal,
    )

    voice = await LanguageRepository(session).tts_voice(translation.target_lang)
    has_tts = bool(voice) and user.settings.tts_enabled

    try:
        await callback.message.edit_reply_markup(
            reply_markup=translation_actions(
                translation_id, has_tts=has_tts, is_favorite=not currently
            )
        )
    except Exception:
        pass

    await callback.answer(
        texts.FAVORITE_REMOVED if currently else texts.FAVORITE_ADDED
    )
