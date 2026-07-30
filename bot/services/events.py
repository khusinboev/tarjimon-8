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

    # Sifat signallari
    FEEDBACK_GIVEN = "feedback.given"

    # Interfeys
    MENU_OPENED = "menu.opened"
    BUTTON_CLICKED = "button.clicked"
    INLINE_QUERY = "inline.query"

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
        EventType.FEEDBACK_GIVEN,
        EventType.ERROR_UNHANDLED,
    }
)


def _should_log(event_type: str) -> bool:
    level = settings.EVENT_LOG_LEVEL
    if level == "off":
        return False
    if level == "important":
        return event_type in IMPORTANT_EVENTS
    return True


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
        source: str = "bot",
        **payload: Any,
    ) -> None:
        if not _should_log(event_type):
            return

        self.session.add(
            Event(
                user_id=user_id,
                chat_id=chat_id,
                session_id=session_id,
                event_type=event_type,
                source=source,
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
