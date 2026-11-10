"""Provayder/kalit uchun "circuit breaker" — ketma-ket yiqilayotgan
provayderni vaqtincha chetlab o'tish.

Muammo (2026-09-04…10): Google Translate kaliti 403 `userRateLimitExceeded`
qaytarib turdi, lekin `pick_key` faqat MUVAFFAQIYATLI belgilarni sanagani
uchun "hajmi tugamagan" deb hisoblab, HAR so'rovda avval Google'ga urardi —
har tarjimaga ~1.1 soniya bekor kechikish, kuniga ~1000 marta.

Qoida: bitta provayder+kalit `WINDOW_SECONDS` ichida `FAILURE_THRESHOLD`
marta ketma-ket yiqilsa — `OPEN_SECONDS` davomida nomzodlar ro'yxatiga
umuman kirmaydi. Muddat tugagach yana sinab ko'riladi (yarim-ochiq holat
alohida modellashtirilmagan — bitta muvaffaqiyat hisoblagichni nolga
tushiradi, bitta xato esa yana sanaydi; bu darajada shu yetarli).

Holat Redis'da (jarayonlar/restart'lar o'rtasida umumiy); Redis yo'q yoki
nosoz bo'lsa — jarayon xotirasida (fail-open: shubhada provayder OCHIQ deb
hisoblanmaydi, ya'ni uriladi — buzilgan Redis tarjimani to'xtatmasin).

Til mos kelmasligi (`supports`) — XATO EMAS, bu yerga tushmaydi.
"""

from __future__ import annotations

import logging
import time

from bot.database.redis import atomic_rate_incr

logger = logging.getLogger(__name__)

FAILURE_THRESHOLD = 3
WINDOW_SECONDS = 120
OPEN_SECONDS = 300


class CircuitBreaker:
    def __init__(self, redis=None):
        self.redis = redis
        # Xotira zaxirasi: key -> (xato soni, oyna tugash vaqti)
        self._mem_fail: dict[str, tuple[int, float]] = {}
        # key -> ochiq holat tugash vaqti
        self._mem_open: dict[str, float] = {}

    @staticmethod
    def _key(name: str, index: int) -> str:
        return f"{name}:{index}"

    async def is_open(self, name: str, index: int) -> bool:
        key = self._key(name, index)
        if self.redis is not None:
            try:
                return bool(await self.redis.exists(f"cb:open:{key}"))
            except Exception:
                pass  # Redis nosoz — xotiradagi holatga tushamiz
        until = self._mem_open.get(key)
        if until is not None and until > time.monotonic():
            return True
        self._mem_open.pop(key, None)
        return False

    async def record_failure(self, name: str, index: int) -> bool:
        """Xatoni sanaydi. `True` — aynan shu xato breaker'ni OCHIB yubordi."""
        key = self._key(name, index)
        if self.redis is not None:
            try:
                count = await atomic_rate_incr(self.redis, f"cb:fail:{key}", WINDOW_SECONDS)
                if count >= FAILURE_THRESHOLD:
                    await self.redis.set(f"cb:open:{key}", "1", ex=OPEN_SECONDS)
                    await self.redis.delete(f"cb:fail:{key}")
                    return True
                return False
            except Exception:
                pass

        now = time.monotonic()
        count, expires_at = self._mem_fail.get(key, (0, 0.0))
        if expires_at < now:
            count = 0
        count += 1
        self._mem_fail[key] = (count, now + WINDOW_SECONDS)
        if count >= FAILURE_THRESHOLD:
            self._mem_open[key] = now + OPEN_SECONDS
            self._mem_fail.pop(key, None)
            return True
        return False

    async def record_success(self, name: str, index: int) -> None:
        key = self._key(name, index)
        if self.redis is not None:
            try:
                await self.redis.delete(f"cb:fail:{key}")
            except Exception:
                pass
        self._mem_fail.pop(key, None)
