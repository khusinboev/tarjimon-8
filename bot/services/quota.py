"""Kunlik limit va spam himoyasi.

Ikki xil cheklov, ikki xil maqsad:
  - **Kunlik limit** (50 tarjima/kun) — resurs sarfini cheklaydi. Haqiqat manbai —
    `daily_usage` jadvali, Redis faqat tezlashtiruvchi kesh.
  - **Rate limit** (60 soniyada 20 so'rov) — flood'dan himoya. Faqat Redis'da,
    chunki yo'qolsa hech narsa buzilmaydi.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.repositories.usage_repository import UsageRepository


@dataclass(slots=True)
class QuotaStatus:
    allowed: bool
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


class QuotaService:
    def __init__(self, session: AsyncSession, redis=None):
        self.usage = UsageRepository(session)
        self.redis = redis

    async def check_translation(
        self, user_id: int, *, limit_override: Optional[int] = None
    ) -> QuotaStatus:
        # `is not None` shart: `0 or DEFAULT` Python'da `0` yolg'on qiymat
        # bo'lgani uchun har doim `DEFAULT` ga tushib qolardi va "0 — cheksiz"
        # degan shartnoma (adminlar, `set_user_limit(..., 0)`) buzilardi.
        limit = (
            limit_override if limit_override is not None else settings.DAILY_TRANSLATION_LIMIT
        )
        # 0 yoki manfiy — cheksiz (adminlar va maxsus userlar uchun).
        if limit <= 0:
            return QuotaStatus(allowed=True, used=0, limit=0)

        row = await self.usage.get(user_id)
        used = row.translations_count if row else 0
        return QuotaStatus(allowed=used < limit, used=used, limit=limit)

    async def check_tts(self, user_id: int) -> QuotaStatus:
        limit = settings.DAILY_TTS_LIMIT
        if limit <= 0:
            return QuotaStatus(allowed=True, used=0, limit=0)

        row = await self.usage.get(user_id)
        used = row.tts_count if row else 0
        return QuotaStatus(allowed=used < limit, used=used, limit=limit)

    async def consume_translation(self, user_id: int, chars: int = 0) -> None:
        await self.usage.increment(user_id, translations=1, chars=chars)

    async def consume_tts(self, user_id: int) -> None:
        await self.usage.increment(user_id, tts=1)

    async def hit_rate_limit(self, user_id: int) -> bool:
        """Flood himoyasi. Redis yo'q bo'lsa — o'tkazib yuboradi (fail-open).

        Fail-open ataylab: Redis tushib qolganda butun bot to'xtab qolgandan
        ko'ra, spam himoyasi vaqtincha ishlamagani afzal.
        """
        if not self.redis:
            return False
        try:
            key = f"rl:{user_id}"
            count = await self.redis.incr(key)
            if count == 1:
                await self.redis.expire(key, settings.RATE_LIMIT_WINDOW)
            return count > settings.RATE_LIMIT_REQUESTS
        except Exception:
            return False
