"""Asosiy tarjima oqimi."""

from __future__ import annotations

import logging
import secrets
from typing import Optional

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import User
from bot.database.repositories.language_repository import LanguageRepository
from bot.database.repositories.translation_repository import TranslationRepository
from bot.keyboards.user import MENU_BUTTONS, translation_actions
from bot.services.events import EventService, EventType
from bot.services.quota import QuotaService
from bot.services.translation import TranslationError, TranslationService
from bot.utils import texts
from bot.utils.text import chunk, content_hash

logger = logging.getLogger(__name__)
router = Router(name="translate")

# Telegram xabar chegarasi 4096; HTML teglar uchun zaxira qoldiramiz.
MESSAGE_LIMIT = 3800
# Tarix o'chirilganda ovoz uchun matnni vaqtincha saqlash muddati.
EPHEMERAL_TTL = 3600


async def store_ephemeral(redis, text: str, lang: str) -> Optional[str]:
    """Saqlanmaydigan tarjima uchun bir martalik token.

    Tarix o'chirilgan bo'lsa tarjima bazaga yozilmaydi, lekin foydalanuvchi
    baribir ovoz eshitishi kerak. Matnni Redis'da qisqa muddat saqlaymiz —
    bu "tarixni saqlama" va'dasini buzmaydi.
    """
    if not redis:
        return None
    token = secrets.token_urlsafe(12)
    try:
        await redis.hset(f"tmp:tts:{token}", mapping={"text": text, "lang": lang})
        await redis.expire(f"tmp:tts:{token}", EPHEMERAL_TTL)
        return token
    except Exception:
        return None


@router.message(F.text & ~F.text.startswith("/") & ~F.text.in_(MENU_BUTTONS))
async def handle_text(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    redis=None,
) -> None:
    text = (message.text or "").strip()
    if not text:
        return

    user_settings = user.settings
    langs = LanguageRepository(session)
    source = user_settings.source_lang
    target = user_settings.target_lang

    quota = QuotaService(session, redis)

    # 1. Flood himoyasi — bazaga tegmaydi, eng arzon tekshiruv birinchi.
    if await quota.hit_rate_limit(user.id):
        await events.log(
            EventType.TRANSLATE_RATE_LIMITED,
            user_id=user.id,
            chat_id=message.chat.id,
            session_id=session_id,
        )
        await message.answer(texts.RATE_LIMITED)
        return

    # 2. Uzunlik.
    if len(text) > settings.TRANSLATION_MAX_CHARS:
        await events.log(
            EventType.TRANSLATE_TOO_LONG,
            user_id=user.id,
            chat_id=message.chat.id,
            session_id=session_id,
            chars=len(text),
        )
        await message.answer(
            texts.TOO_LONG.format(length=len(text), limit=settings.TRANSLATION_MAX_CHARS)
        )
        return

    # 3. Bir xil til.
    if source == target:
        lang = await langs.by_code(target)
        await message.answer(
            texts.SAME_LANGUAGE.format(lang=lang.name_uz if lang else target)
        )
        return

    # 4. Kunlik limit. Adminlarga cheklov qo'yilmaydi.
    limit_override = user_settings.daily_limit_override
    if user.role in ("admin", "owner"):
        limit_override = 0
    status = await quota.check_translation(user.id, limit_override=limit_override)
    if not status.allowed:
        await events.log(
            EventType.TRANSLATE_QUOTA_EXCEEDED,
            user_id=user.id,
            chat_id=message.chat.id,
            session_id=session_id,
            limit=status.limit,
            used=status.used,
        )
        await message.answer(texts.QUOTA_EXCEEDED.format(limit=status.limit))
        return

    await events.log(
        EventType.TRANSLATE_REQUESTED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        input_kind="text",
        chars=len(text),
        src=source,
        dst=target,
    )

    repo = TranslationRepository(session)
    source_hash = content_hash(text, source, target)

    # Yaqinda shu matn tarjima qilingan bo'lsa — foydalanuvchi natijadan qoniqmagan.
    # Bu sifat signali; uni faqat shu yerda ushlash mumkin.
    previous = await repo.find_recent_by_hash(user.id, source_hash, within_seconds=60)
    if previous is not None:
        await repo.add_signal(
            translation_id=previous.id, user_id=user.id, signal="retranslated"
        )

    service = TranslationService(redis)
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        result = await service.translate(text, source, target)
    except TranslationError as exc:
        # Muvaffaqiyatsiz urinish ham yoziladi — provayder sog'lig'ini kuzatish uchun.
        await repo.create(
            user_id=user.id,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            input_kind="text",
            source_lang_requested=source,
            target_lang=target,
            source_text=text,
            source_hash=source_hash,
            source_chars=len(text),
            provider=settings.TRANSLATION_PROVIDER,
            status="timeout" if exc.code == "timeout" else "error",
            error_code=exc.code,
            error_message=str(exc)[:500],
        )
        await events.log(
            EventType.TRANSLATE_FAILED,
            user_id=user.id,
            chat_id=message.chat.id,
            session_id=session_id,
            error_code=exc.code,
            provider=settings.TRANSLATION_PROVIDER,
        )
        await message.answer(texts.ERRORS.get(exc.code, texts.ERROR_DEFAULT))
        return

    detected = result.source_lang_detected or (source if source != "auto" else None)

    translation_id: Optional[int] = None
    if user_settings.save_history:
        translation = await repo.create(
            user_id=user.id,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            input_kind="text",
            source_lang_requested=source,
            source_lang_detected=detected,
            target_lang=target,
            source_text=text,
            target_text=result.text,
            source_hash=source_hash,
            source_chars=len(text),
            target_chars=len(result.text),
            provider=result.provider,
            provider_model=result.provider_model,
            status="success",
            latency_ms=result.latency_ms,
            cache_hit=result.cache_hit,
        )
        translation_id = translation.id

    await quota.consume_translation(user.id, chars=len(text))

    await events.log(
        EventType.TRANSLATE_SUCCEEDED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        translation_id=translation_id,
        latency_ms=result.latency_ms,
        provider=result.provider,
        detected=detected,
        cache_hit=result.cache_hit,
    )

    voice = await langs.tts_voice(target)
    has_tts = bool(voice) and user_settings.tts_enabled

    # Uzun tarjima bo'laklab yuboriladi, tugmalar oxirgi bo'lakka biriktiriladi.
    parts = chunk(result.text, MESSAGE_LIMIT)
    markup = None

    if translation_id is not None:
        markup = translation_actions(translation_id, has_tts=has_tts, is_favorite=False)
    elif has_tts:
        token = await store_ephemeral(redis, result.text, target)
        if token:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🔊 Ovoz", callback_data=f"tr:tmp:{token}")]
                ]
            )

    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1
        await message.answer(part, reply_markup=markup if is_last else None)

    if user_settings.tts_auto and has_tts:
        from bot.handlers.user.tts import send_voice

        await send_voice(
            message=message,
            session=session,
            user=user,
            events=events,
            session_id=session_id,
            redis=redis,
            text=result.text,
            lang=target,
            voice=voice,
            translation_id=translation_id,
        )


@router.message(F.voice | F.audio | F.video_note | F.photo | F.document | F.video)
async def handle_unsupported(message: Message) -> None:
    """Hozircha faqat matn. Stub emas — aniq va halol javob."""
    await message.answer(texts.UNSUPPORTED_INPUT)
