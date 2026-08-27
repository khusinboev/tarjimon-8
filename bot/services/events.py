"""Voqealar taksonomiyasi va yozuvchi servis.

`events` jadvali JSONB payload'li bitta keng jadval bo'lgani uchun yangi voqea
turi qo'shish migratsiya talab qilmaydi. Tip xavfsizligini shu modul beradi:
`event_type` sifatida faqat shu yerdagi konstantalar ishlatilsin.

Batafsil: SXEMA.md → Blok 3.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import Event


class EventType:
    """`domain.action` formatidagi voqea nomlari."""

    # Foydalanuvchi
    USER_START = "user.start"
    USER_BLOCKED_BOT = "user.blocked_bot"
    USER_UNBLOCKED_BOT = "user.unblocked_bot"

    # Majburiy obuna
    SUBSCRIPTION_CHECKED = "subscription.checked"
    SUBSCRIPTION_JOINED = "subscription.joined"

    # Sozlamalar
    LANG_SELECTED = "lang.selected"
    LANG_SWAPPED = "lang.swapped"
    SETTINGS_CHANGED = "settings.changed"

    # Tarjima
    TRANSLATE_REQUESTED = "translate.requested"
    TRANSLATE_SUCCEEDED = "translate.succeeded"
    TRANSLATE_FAILED = "translate.failed"
    TRANSLATE_QUOTA_EXCEEDED = "translate.quota_exceeded"
    TRANSLATE_RATE_LIMITED = "translate.rate_limited"
    TRANSLATE_TOO_LONG = "translate.too_long"

    # Ovoz
    TTS_REQUESTED = "tts.requested"
    TTS_SUCCEEDED = "tts.succeeded"
    TTS_FAILED = "tts.failed"
    TTS_QUOTA_EXCEEDED = "tts.quota_exceeded"

    # Rasmdan tarjima (OCR)
    IMAGE_QUOTA_EXCEEDED = "image.quota_exceeded"
    IMAGE_OCR_REQUESTED = "image.ocr_requested"
    IMAGE_OCR_SUCCEEDED = "image.ocr_succeeded"
    IMAGE_OCR_FAILED = "image.ocr_failed"
    IMAGE_OCR_EMPTY = "image.ocr_empty"

    # Sifat signallari
    FEEDBACK_GIVEN = "feedback.given"

    # Interfeys
    MENU_OPENED = "menu.opened"
    BUTTON_CLICKED = "button.clicked"
    INLINE_QUERY = "inline.query"

    # Adminga murojaat
    SUPPORT_MESSAGE_SENT = "support.message_sent"
    SUPPORT_REPLY_SENT = "support.reply_sent"

    # Homiylik
    DONATE_OPENED = "donate.opened"
    DONATE_INVOICE_SENT = "donate.invoice_sent"
    DONATE_PAID = "donate.paid"

    # VIP va referal
    PREMIUM_GRANTED = "premium.granted"
    USER_REFERRED = "user.referred"
    REFERRAL_BONUS_GRANTED = "referral.bonus_granted"

    # Tizim
    BROADCAST_DELIVERED = "broadcast.delivered"
    ERROR_UNHANDLED = "error.unhandled"


# EVENT_LOG_LEVEL="important" da yoziladigan voqealar. Qolganlari faqat "all" da.
IMPORTANT_EVENTS = frozenset(
    {
        EventType.USER_START,
        EventType.USER_BLOCKED_BOT,
        EventType.USER_UNBLOCKED_BOT,
        EventType.SUBSCRIPTION_CHECKED,
        EventType.SUBSCRIPTION_JOINED,
        EventType.LANG_SELECTED,
        EventType.SETTINGS_CHANGED,
        EventType.TRANSLATE_REQUESTED,
        EventType.TRANSLATE_SUCCEEDED,
        EventType.TRANSLATE_FAILED,
        EventType.TRANSLATE_QUOTA_EXCEEDED,
        EventType.TRANSLATE_RATE_LIMITED,
        EventType.TTS_REQUESTED,
        EventType.TTS_SUCCEEDED,
        EventType.TTS_FAILED,
        # OCR tashqi provayderga pul/hajm sarflaydi — har biri kuzatilishi kerak.
        EventType.IMAGE_QUOTA_EXCEEDED,
        EventType.IMAGE_OCR_REQUESTED,
        EventType.IMAGE_OCR_SUCCEEDED,
        EventType.IMAGE_OCR_FAILED,
        EventType.FEEDBACK_GIVEN,
        # Murojaat matni faqat shu voqeada saqlanadi — admin Telegram'da
        # o'tkazib yuborsa yagona nusxa bo'lib qoladi, shuning uchun "muhim".
        EventType.SUPPORT_MESSAGE_SENT,
        EventType.SUPPORT_REPLY_SENT,
        # To'lov voqeasi har doim yoziladi — pul harakati hisobga olinishi kerak.
        EventType.DONATE_PAID,
        # Mukofot berilishi ham pul/imtiyoz harakati bilan bir xil darajada muhim.
        EventType.PREMIUM_GRANTED,
        EventType.REFERRAL_BONUS_GRANTED,
        EventType.ERROR_UNHANDLED,
    }
)


def _should_log(event_type: str) -> bool:
    # Katta-kichik harfga sezgir emas — aks holda `.env`da "Off"/"OFF" kabi
    # yozilsa, jim tarzda "hammasini yoz"ga tushib qolardi (aynan
    # o'chirishni maqsad qilgan sozlama teskarisiga ishlaydi).
    level = settings.EVENT_LOG_LEVEL.strip().lower()
    if level == "off":
        return False
    if level == "important":
        return event_type in IMPORTANT_EVENTS
    return True


# `events.source` ustunining ruxsat etilgan qiymatlari (bazada CHECK bilan ham
# cheklangan). Bu yerda ham tekshiramiz, chunki noto'g'ri qiymat bazaga
# yetganda CheckViolationError butun tranzaksiyani yiqitadi — ya'ni voqea
# yozish asosiy oqimni buzadi, aynan bo'lmasligi kerak narsa.
EVENT_SOURCES = frozenset({"bot", "admin", "system", "webhook"})


class EventService:
    """Voqealarni yozadi. Yozish hech qachon asosiy oqimni to'xtatmasligi kerak."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def log(
        self,
        event_type: str,
        *,
        user_id: Optional[int] = None,
        chat_id: Optional[int] = None,
        session_id: Optional[uuid.UUID] = None,
        event_source: str = "bot",
        **payload: Any,
    ) -> None:
        """Voqeani yozadi. Nomlangan argumentlardan boshqasi `payload` ga tushadi.

        Diqqat — `event_source`, `source` emas: `source` payload kaliti sifatida
        juda tabiiy ("referal manbai", "manba tili") va u ustun nomi bilan
        to'qnashib, jimgina ustunga yozilib qolardi. Natijada `lang.swapped`
        voqeasi `source='uz'` bilan yozilib CHECK cheklovini buzgan va
        almashtirish tugmasi ishlamay qolgan edi.
        """
        if not _should_log(event_type):
            return

        if event_source not in EVENT_SOURCES:
            # Baland ovozda yiqilamiz: bu kod xatosi, ma'lumot xatosi emas.
            raise ValueError(
                f"Noto'g'ri event_source: {event_source!r}. "
                f"Ruxsat etilgan: {', '.join(sorted(EVENT_SOURCES))}"
            )

        self.session.add(
            Event(
                user_id=user_id,
                chat_id=chat_id,
                session_id=session_id,
                event_type=event_type,
                source=event_source,
                payload=payload,
            )
        )
        # commit() chaqirilmaydi — chaqiruvchi tranzaksiyaga qo'shiladi.
        # Bu bitta xabar ishlovi uchun bitta commit degani.


class SessionTracker:
    """Foydalanuvchi harakatlarini seanslarga guruhlaydi.

    Ketma-ket harakatlar orasida SESSION_WINDOW_MINUTES dan ko'p tanaffus bo'lsa —
    yangi seans. Bu ML uchun muhim: bitta seansdagi voqealar ketma-ketligi
    foydalanuvchi yo'lini (funnel) tiklashga imkon beradi.

    Redis'da saqlanadi, chunki bu efemer holat — yo'qolsa, faqat seans bo'linadi.
    """

    def __init__(self, redis):
        self.redis = redis
        self.ttl = settings.SESSION_WINDOW_MINUTES * 60

    async def get(self, user_id: int) -> uuid.UUID:
        key = f"session:{user_id}"
        try:
            existing = await self.redis.get(key)
            if existing:
                # TTL ni uzaytiramiz — faollik davom etyapti.
                await self.redis.expire(key, self.ttl)
                return uuid.UUID(existing.decode() if isinstance(existing, bytes) else existing)

            new_id = uuid.uuid4()
            await self.redis.set(key, str(new_id), ex=self.ttl)
            return new_id
        except Exception:
            # Redis yo'q bo'lsa ham bot ishlashda davom etsin.
            return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def day_bounds(when: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """UTC kun chegaralari — kunlik limit hisobi uchun."""
    when = when or utcnow()
    start = when.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)
