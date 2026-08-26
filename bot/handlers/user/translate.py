"""Asosiy tarjima oqimi."""

from __future__ import annotations

import logging
from types import ModuleType
from typing import Optional

from aiogram import F, Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import User
from bot.database.repositories.language_repository import LanguageRepository
from bot.database.repositories.translation_repository import TranslationRepository
from bot.keyboards.user import quota_exceeded_keyboard, translation_actions
from bot.locales import MENU_BUTTONS
from bot.services.admin_alerts import alert_admins_once
from bot.services.events import EventService, EventType
from bot.services.ocr import OcrError, extract_text
from bot.services.quota import QuotaService, resolve_limit_override
from bot.services.referral import grant_referral_bonus
from bot.services.system_config import get_effective_limits
from bot.services.translation import TranslationError, TranslationService
from bot.utils.content import extract
from bot.utils.text import chunk_html_safe, content_hash, html_escape

logger = logging.getLogger(__name__)
router = Router(name="translate")

# Telegram xabar chegarasi 4096; `<code>` teglari va zaxira uchun kamaytiramiz.
MESSAGE_LIMIT = 3800


async def _translate(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
    redis,
    *,
    text: str,
    input_kind: str,
    manage_quota: bool = True,
    manage_rate_limit: bool = True,
    ocr_provider: Optional[str] = None,
    ocr_key_index: Optional[int] = None,
) -> None:
    """Tarjima oqimi. Matn qayerdan kelganidan qat'i nazar bir xil.

    `input_kind` faqat yozuvga tushadi — oqimga ta'sir qilmaydi.

    `manage_quota=False` — rasm (OCR) oqimi uchun: kvota OCR chaqiruvidan
    OLDIN, chaqiruvchida allaqachon tekshirilgan va sarflangan (rasm limiti
    matn limitidan butunlay alohida hisoblanadi), shuning uchun bu yerda
    yana matn kvotasini tekshirish/sarflash noto'g'ri bo'lardi.

    `manage_rate_limit=False` — xuddi shu sababdan rasm oqimi uchun: flood
    himoyasi ham OCR chaqiruvidan OLDIN chaqiruvchida tekshiriladi (aks
    holda bitta rasm 2 marta flood-limit sarflab, matn yuborgan
    foydalanuvchiga nisbatan 2 barobar tezroq bloklanardi).
    """
    text = text.strip()
    if not text:
        return

    user_settings = user.settings
    langs = LanguageRepository(session)
    source = user_settings.source_lang
    target = user_settings.target_lang

    quota = QuotaService(session, redis)

    # 1. Flood himoyasi — bazaga tegmaydi, eng arzon tekshiruv birinchi.
    if manage_rate_limit and await quota.hit_rate_limit(user.id):
        await events.log(
            EventType.TRANSLATE_RATE_LIMITED,
            user_id=user.id,
            chat_id=message.chat.id,
            session_id=session_id,
        )
        await message.answer(t.RATE_LIMITED)
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
            t.TOO_LONG.format(length=len(text), limit=settings.TRANSLATION_MAX_CHARS)
        )
        return

    # 3. Bir xil til.
    if source == target:
        lang = await langs.by_code(target)
        await message.answer(
            t.SAME_LANGUAGE.format(lang=lang.name_native if lang else target)
        )
        return

    # 4. Kunlik limit. Ustunlik: admin rol > qo'lda qo'yilgan override > VIP.
    # Rasm oqimida bu allaqachon chaqiruvchida tekshirilgan — takrorlanmaydi.
    # ATOMIK band qilinadi (tekshirish+oshirish bitta so'rovda) — aks holda
    # ikkita deyarli bir vaqtdagi so'rov (double-tap/skript) ikkalasi ham
    # "hali limitdan oshmagan"ni ko'rib, limitni chetlab o'tishi mumkin edi.
    # Muvaffaqiyatsiz bo'lsa (`except TranslationError` pastda) ortga
    # qaytariladi (`release_translation`).
    if manage_quota:
        limit_override = resolve_limit_override(user, kind="translation", vip_value=0)
        status = await quota.try_reserve_translation(
            user.id, chars=len(text), limit_override=limit_override
        )
        if not status.allowed:
            await events.log(
                EventType.TRANSLATE_QUOTA_EXCEEDED,
                user_id=user.id,
                chat_id=message.chat.id,
                session_id=session_id,
                limit=status.limit,
                used=status.used,
            )
            await message.answer(
                t.QUOTA_EXCEEDED.format(limit=status.limit),
                reply_markup=quota_exceeded_keyboard(t),
            )
            return

    await events.log(
        EventType.TRANSLATE_REQUESTED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        input_kind=input_kind,
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

    service = TranslationService(redis, bot=message.bot)
    await message.bot.send_chat_action(message.chat.id, "typing")

    try:
        result = await service.translate(session, text, source, target)
    except TranslationError as exc:
        if manage_quota:
            # Band qilingan joy (yuqorida `try_reserve_translation`) ortga
            # qaytariladi — muvaffaqiyatsiz urinish kvotadan sarflanmasligi
            # kerak (avvalgi xulq-atvor bilan bir xil: faqat muvaffaqiyatli
            # urinish hisoblanadi).
            await quota.release_translation(user.id, chars=len(text))
        # Muvaffaqiyatsiz urinish ham yoziladi — provayder sog'lig'ini kuzatish uchun.
        await repo.create(
            user_id=user.id,
            chat_id=message.chat.id,
            chat_type=message.chat.type,
            input_kind=input_kind,
            source_lang_requested=source,
            target_lang=target,
            source_text=text,
            source_hash=source_hash,
            source_chars=len(text),
            provider=settings.TRANSLATION_PROVIDER,
            ocr_provider=ocr_provider,
            ocr_key_index=ocr_key_index,
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
        await message.answer(t.ERRORS.get(exc.code, t.ERROR_DEFAULT))
        return

    detected = result.source_lang_detected or (source if source != "auto" else None)

    # Har bir tarjima yoziladi: tugmalar (ovoz, almashtirish) shu yozuvga
    # tayanadi va bu jadval ML uchun asosiy manba.
    translation = await repo.create(
        user_id=user.id,
        chat_id=message.chat.id,
        chat_type=message.chat.type,
        input_kind=input_kind,
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
        provider_key_index=result.provider_key_index,
        ocr_provider=ocr_provider,
        ocr_key_index=ocr_key_index,
        status="success",
        latency_ms=result.latency_ms,
        cache_hit=result.cache_hit,
    )

    # Kvota allaqachon yuqorida (`try_reserve_translation`) band qilingan —
    # muvaffaqiyat bo'lgani uchun band qilingan joy shunday qoladi,
    # qo'shimcha oshirish shart emas.

    # Referal bonusi: faqat yangi userning BIRINCHI muvaffaqiyatli tarjimasida
    # ishlaydi (`grant_referral_bonus` ichida bayroq bilan tekshiriladi).
    # Tarjima natijasidan oldin chaqiramiz — bonus xabari (agar bo'lsa) ham,
    # tarjima natijasi ham foydalanuvchiga bir xil javob ichida ketadi.
    await grant_referral_bonus(message, session, events, user, session_id)

    await events.log(
        EventType.TRANSLATE_SUCCEEDED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        translation_id=translation.id,
        latency_ms=result.latency_ms,
        provider=result.provider,
        detected=detected,
        cache_hit=result.cache_hit,
    )

    voice = await langs.tts_voice(target)
    # Ovoz tugmasi sozlanmaydi — til qo'llab-quvvatlasa doim ko'rinadi.
    has_tts = bool(voice)

    markup = translation_actions(
        t,
        translation.id,
        has_tts=has_tts,
        source=await langs.by_code(source),
        target=await langs.by_code(target),
    )

    # Tarjima `<code>` ichida yuboriladi — Telegram'da ustiga bosib nusxa olinadi.
    # Uzun tarjima bo'laklanadi, tugmalar oxirgi bo'lakka biriktiriladi.
    parts = chunk_html_safe(result.text, MESSAGE_LIMIT)
    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1
        await message.answer(
            f"<code>{html_escape(part)}</code>",
            reply_markup=markup if is_last else None,
        )


# ─────────────────────────────────────────────────────────────
#  Kirish nuqtalari
# ─────────────────────────────────────────────────────────────
@router.message(F.text & ~F.text.startswith("/") & ~F.text.in_(MENU_BUTTONS))
async def handle_text(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
    redis=None,
) -> None:
    await _translate(
        message, session, user, events, session_id, t, redis,
        text=message.text or "", input_kind="text",
    )


# Matn saqlashi mumkin bo'lgan qolgan hamma narsa: media izohlari, "post"
# (`rich_message`), so'rovnoma va checklist. Ajratish `utils/content.py` da —
# u tuzilishga qarab yuradi, chunki bu turlar tez o'zgarmoqda.
@router.message(F.caption | F.rich_message | F.poll | F.checklist)
async def handle_content(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
    redis=None,
) -> None:
    extracted = extract(message)
    if extracted is None:
        await message.answer(t.NO_TEXT_FOUND)
        return

    text, input_kind = extracted
    await _translate(
        message, session, user, events, session_id, t, redis,
        text=text, input_kind=input_kind,
    )


# Izohsiz rasm — OCR bilan matnini o'qib tarjima qilamiz. Izohli rasm
# yuqoridagi `handle_content` ga tushadi (caption tarjima qilinadi, rasm
# o'zi OCR qilinmaydi) — ikkalasi bir-birini hech qachon qamrab olmaydi.
@router.message(F.photo & ~F.caption)
async def handle_photo(
    message: Message,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
    redis=None,
) -> None:
    """Rasmdagi matnni OCR bilan ajratib tarjima qiladi.

    Kvota OCR chaqiruvidan OLDIN tekshiriladi — bu tashqi provayderga
    pul/hajm sarflanishining oldini oladi (foydalanuvchi limitdan oshgan
    bo'lsa OCR umuman chaqirilmaydi). Matn/ovoz limitidan butunlay alohida
    hisoblanadi (`images_count`); VIP uni cheksiz emas, faqat kengaytirilgan
    (`DAILY_IMAGE_LIMIT_VIP`) qiladi — OCR pulga tushishi mumkin.
    """
    quota = QuotaService(session, redis)

    if await quota.hit_rate_limit(user.id):
        await events.log(
            EventType.TRANSLATE_RATE_LIMITED,
            user_id=user.id,
            chat_id=message.chat.id,
            session_id=session_id,
        )
        await message.answer(t.RATE_LIMITED)
        return

    limits = await get_effective_limits(session)
    limit_override = resolve_limit_override(user, kind="image", vip_value=limits.image_vip)
    # ATOMIK band qilinadi — TOCTOU'siz (izoh: `_translate`dagi matn
    # limitining bir xil tuzatishi). OCR muvaffaqiyatsiz bo'lsa pastda
    # ortga qaytariladi.
    status = await quota.try_reserve_image(user.id, limit_override=limit_override)
    if not status.allowed:
        await events.log(
            EventType.IMAGE_QUOTA_EXCEEDED,
            user_id=user.id,
            chat_id=message.chat.id,
            session_id=session_id,
            limit=status.limit,
            used=status.used,
        )
        await message.answer(
            t.IMAGE_QUOTA_EXCEEDED.format(limit=status.limit),
            reply_markup=quota_exceeded_keyboard(t),
        )
        return

    await message.bot.send_chat_action(message.chat.id, "typing")
    await events.log(
        EventType.IMAGE_OCR_REQUESTED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
    )

    photo_file = await message.bot.download(message.photo[-1].file_id)
    image_bytes = photo_file.read()

    try:
        result = await extract_text(session, image_bytes)
    except OcrError as exc:
        # OCR o'zi ishlamadi (hech narsa sarflanmadi) — band qilingan joy
        # ortga qaytariladi, aks holda muvaffaqiyatsiz urinish ham rasm
        # kvotasidan yeb qo'yardi.
        await quota.release_image(user.id)
        await events.log(
            EventType.IMAGE_OCR_FAILED,
            user_id=user.id,
            chat_id=message.chat.id,
            session_id=session_id,
            error_code=exc.code,
        )
        if exc.code == "quota_exhausted":
            await alert_admins_once(
                message.bot, redis, "ocr_quota_exhausted",
                "⚠️ <b>OCR.Space</b>ning barcha kalitlari bu oy bepul hajmidan "
                "oshdi — rasmdan tarjima Vision sozlanmagan bo'lsa ishlamay "
                "qolishi mumkin.",
            )
        elif exc.code != "not_configured":
            # "not_configured" — doimiy holat (OCR umuman sozlanmagan),
            # alohida ogohlantirish shart emas. Boshqa har qanday xato —
            # rasmdan tarjima BUTUNLAY ishlamayapti degani.
            await alert_admins_once(
                message.bot, redis, "ocr_all_failed",
                f"🆘 <b>Rasmdan tarjima ishlamayapti</b> — oxirgi xato: "
                f"{exc.code} — {exc}",
            )
        text = t.IMAGE_OCR_UNAVAILABLE if exc.code == "not_configured" else t.IMAGE_OCR_FAILED
        await message.answer(text)
        return

    if result.ocrspace_quota_exhausted:
        await alert_admins_once(
            message.bot, redis, "ocr_quota_exhausted",
            "⚠️ <b>OCR.Space</b>ning barcha kalitlari bu oy bepul hajmidan "
            "oshdi — hozircha Google Vision jim qoplab turibdi (bepul "
            "hajmi kichikroq, keyin pullik bo'lishi mumkin).",
        )

    # OCR chaqiruvi allaqachon amalga oshdi (pul/hajm sarflandi) — kvota
    # yuqorida band qilingan joy sifatida allaqachon sarflangan (natija
    # bo'sh bo'lsa ham qaytarilmaydi, aks holda bo'sh rasm yuborib cheksiz
    # urinish mumkin bo'lardi).

    if not result.text.strip():
        await events.log(
            EventType.IMAGE_OCR_EMPTY,
            user_id=user.id,
            chat_id=message.chat.id,
            session_id=session_id,
            provider=result.provider,
        )
        await message.answer(t.IMAGE_NO_TEXT_FOUND)
        return

    await events.log(
        EventType.IMAGE_OCR_SUCCEEDED,
        user_id=user.id,
        chat_id=message.chat.id,
        session_id=session_id,
        provider=result.provider,
        key_index=result.key_index,
        chars=len(result.text),
    )

    await _translate(
        message, session, user, events, session_id, t, redis,
        text=result.text, input_kind="photo",
        manage_quota=False, manage_rate_limit=False,
        ocr_provider=result.provider, ocr_key_index=result.key_index,
    )


@router.message(
    F.voice | F.audio | F.video_note | F.document | F.video
    | F.sticker | F.animation | F.story | F.location | F.contact
)
async def handle_unsupported(message: Message, t: ModuleType) -> None:
    """Matni umuman yo'q kontent.

    Izohli media yuqoridagi `handle_content` ga tushadi, izohsiz rasm
    yuqoridagi `handle_photo` ga (OCR) — bu yerga faqat OCR qilib
    bo'lmaydigan turlar yetib keladi.
    """
    await message.answer(t.UNSUPPORTED_INPUT)
