"""Ovoz (TTS) handlerlari."""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import TtsRequest, User
from bot.database.repositories.language_repository import LanguageRepository
from bot.database.repositories.translation_repository import TranslationRepository
from bot.services.events import EventService, EventType
from bot.services.quota import QuotaService
from bot.services.tts import TtsError, TtsService
from bot.utils import texts
from bot.utils.text import text_hash, truncate

logger = logging.getLogger(__name__)
router = Router(name="tts")


async def send_voice(
    *,
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    redis,
    text: str,
    lang: str,
    voice: str,
    translation_id: Optional[int] = None,
) -> bool:
    """Ovozni tayyorlab yuboradi. Muvaffaqiyat holatini qaytaradi.

    Telegram bir marta yuborilgan faylni `file_id` orqali qayta yuborishga ruxsat
    beradi. Shuning uchun avval keshni tekshiramiz — bu generatsiya vaqtini ham,
    provayder yukini ham tejaydi.
    """
    quota = QuotaService(session, redis)

    status = await quota.check_tts(user.id)
    if not status.allowed and user.role not in ("admin", "owner"):
        await events.log(
            EventType.TTS_QUOTA_EXCEEDED,
            user_id=user.id,
            session_id=session_id,
            limit=status.limit,
        )
        await message.answer(texts.TTS_QUOTA_EXCEEDED.format(limit=status.limit))
        return False

    if len(text) > settings.TTS_MAX_CHARS:
        await message.answer(texts.TTS_TOO_LONG.format(limit=settings.TTS_MAX_CHARS))
        return False

    service = TtsService(redis)
    key = text_hash(text, lang, voice)

    await events.log(
        EventType.TTS_REQUESTED,
        user_id=user.id,
        session_id=session_id,
        translation_id=translation_id,
        lang=lang,
        chars=len(text),
    )

    cached_file_id = await service.get_cached_file_id(key)
    if cached_file_id:
        try:
            await message.answer_voice(cached_file_id)
            await quota.consume_tts(user.id)
            await events.log(
                EventType.TTS_SUCCEEDED,
                user_id=user.id,
                session_id=session_id,
                translation_id=translation_id,
                lang=lang,
                cache_hit=True,
            )
            return True
        except Exception:
            # file_id eskirgan bo'lishi mumkin — qaytadan generatsiya qilamiz.
            logger.info("TTS kesh file_id ishlamadi, qayta generatsiya", exc_info=True)

    await message.bot.send_chat_action(message.chat.id, "record_voice")

    try:
        result = await service.synthesize(text, voice)
    except TtsError as exc:
        session.add(
            TtsRequest(
                user_id=user.id,
                translation_id=translation_id,
                text=truncate(text, 4000),
                text_hash=key,
                lang=lang,
                voice=voice,
                provider=settings.TTS_PROVIDER,
                status="timeout" if exc.code == "timeout" else "error",
                error_code=exc.code,
            )
        )
        await events.log(
            EventType.TTS_FAILED,
            user_id=user.id,
            session_id=session_id,
            translation_id=translation_id,
            error_code=exc.code,
        )
        await message.answer(texts.TTS_ERRORS.get(exc.code, texts.TTS_ERROR_DEFAULT))
        return False

    sent = await message.answer_voice(
        BufferedInputFile(result.audio, filename="tarjima.mp3")
    )

    file_id = sent.voice.file_id if sent.voice else None
    if file_id:
        await service.cache_file_id(key, file_id)

    session.add(
        TtsRequest(
            user_id=user.id,
            translation_id=translation_id,
            text=truncate(text, 4000),
            text_hash=key,
            lang=lang,
            voice=result.voice,
            provider=result.provider,
            file_size=len(result.audio),
            telegram_file_id=file_id,
            status="success",
            latency_ms=result.latency_ms,
        )
    )

    await quota.consume_tts(user.id)

    if translation_id is not None:
        # Ovoz eshitish — musbat sifat signali.
        await TranslationRepository(session).add_signal(
            translation_id=translation_id, user_id=user.id, signal="tts_played"
        )

    await events.log(
        EventType.TTS_SUCCEEDED,
        user_id=user.id,
        session_id=session_id,
        translation_id=translation_id,
        lang=lang,
        latency_ms=result.latency_ms,
        cache_hit=False,
    )
    return True


@router.callback_query(F.data.startswith("tr:tts:"))
async def on_tts(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    redis=None,
) -> None:
    try:
        translation_id = int(callback.data.split(":")[2])
    except (IndexError, ValueError):
        await callback.answer(texts.TRANSLATION_NOT_FOUND, show_alert=True)
        return

    repo = TranslationRepository(session)
    translation = await repo.get(translation_id)

    if translation is None or translation.user_id != user.id or not translation.target_text:
        await callback.answer(texts.TRANSLATION_NOT_FOUND, show_alert=True)
        return

    voice = await LanguageRepository(session).tts_voice(translation.target_lang)
    if not voice:
        await callback.answer(texts.TTS_ERRORS["no_voice"], show_alert=True)
        return

    await callback.answer()
    await send_voice(
        message=callback.message,
        session=session,
        user=user,
        events=events,
        session_id=session_id,
        redis=redis,
        text=translation.target_text,
        lang=translation.target_lang,
        voice=voice,
        translation_id=translation.id,
    )


@router.callback_query(F.data.startswith("tr:tmp:"))
async def on_tts_ephemeral(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    redis=None,
) -> None:
    """Tarix o'chirilgan foydalanuvchi uchun ovoz — matn Redis'da vaqtincha yotadi."""
    token = callback.data.split(":", 2)[2] if callback.data.count(":") >= 2 else ""
    if not token or not redis:
        await callback.answer(texts.TRANSLATION_NOT_FOUND, show_alert=True)
        return

    try:
        stored = await redis.hgetall(f"tmp:tts:{token}")
    except Exception:
        stored = None

    if not stored:
        await callback.answer(texts.TRANSLATION_NOT_FOUND, show_alert=True)
        return

    def value(key: str) -> str:
        raw = stored.get(key) or stored.get(key.encode())
        if isinstance(raw, bytes):
            return raw.decode()
        return raw or ""

    text = value("text")
    lang = value("lang")
    if not text or not lang:
        await callback.answer(texts.TRANSLATION_NOT_FOUND, show_alert=True)
        return

    voice = await LanguageRepository(session).tts_voice(lang)
    if not voice:
        await callback.answer(texts.TTS_ERRORS["no_voice"], show_alert=True)
        return

    await callback.answer()
    await send_voice(
        message=callback.message,
        session=session,
        user=user,
        events=events,
        session_id=session_id,
        redis=redis,
        text=text,
        lang=lang,
        voice=voice,
        translation_id=None,
    )
