"""Admin panelidan sozlanadigan umumiy (hamma uchun) standart limitlar.

`system_settings` bitta qatorli (id=1) jadval. Har bir maydonda `NULL` —
`.env` dagi standartni ishlat degani, boshqa qiymat qo'yilsa o'sha ustun
turadi. Bu `UserSettings.*_override` bilan bir xil "None = yo'q, 0 = cheksiz"
konvensiyasi, faqat bitta foydalanuvchi emas — HAMMA uchun.

Bu funksiyalar har bir tarjima/ovoz/rasm so'rovida chaqiriladi (issiq yo'l),
shuning uchun jarayon ichida qisqa muddat keshlanadi. Admin qiymatni
o'zgartirganda `invalidate_cache()` darhol chaqiriladi — o'zgarish keshning
tugashini kutmay ishlaydi.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import SystemSettings

CACHE_TTL = 30  # soniya

# Admin tahrirlashi mumkin bo'lgan maydonlar — panel shu ro'yxatdan tanlaydi.
EDITABLE_FIELDS = (
    "daily_translation_limit",
    "daily_tts_limit",
    "daily_image_limit_free",
    "daily_image_limit_vip",
)

_cache: Optional["SystemLimits"] = None
_cache_at: float = 0.0


@dataclass(slots=True, frozen=True)
class SystemLimits:
    translation: int
    tts: int
    image_free: int
    image_vip: int


def invalidate_cache() -> None:
    global _cache
    _cache = None


async def get_row(session: AsyncSession) -> Optional[SystemSettings]:
    """Xom DB qatorini qaytaradi (admin ko'rinishida "o'zgartirilgan/standart"
    belgisini ko'rsatish uchun kerak) — `NULL` qiymatlar shu yerda ham `NULL`."""
    return (
        await session.execute(select(SystemSettings).where(SystemSettings.id == 1))
    ).scalar_one_or_none()


async def get_effective_limits(session: AsyncSession) -> SystemLimits:
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and (now - _cache_at) < CACHE_TTL:
        return _cache

    row = await get_row(session)

    def pick(db_value: Optional[int], default: int) -> int:
        # `is not None` shart: `0` — admin ataylab "hammaga cheksiz" qo'ygan
        # bo'lishi mumkin, `or` operatori bu holatni default bilan almashtirib
        # qo'yardi (xuddi `quota.py` dagi eski falsy-zero xatosi kabi).
        return db_value if db_value is not None else default

    limits = SystemLimits(
        translation=pick(row.daily_translation_limit if row else None, settings.DAILY_TRANSLATION_LIMIT),
        tts=pick(row.daily_tts_limit if row else None, settings.DAILY_TTS_LIMIT),
        image_free=pick(row.daily_image_limit_free if row else None, settings.DAILY_IMAGE_LIMIT_FREE),
        image_vip=pick(row.daily_image_limit_vip if row else None, settings.DAILY_IMAGE_LIMIT_VIP),
    )
    _cache = limits
    _cache_at = now
    return limits


async def set_limit(session: AsyncSession, field: str, value: Optional[int]) -> None:
    """Bitta maydonni yangilaydi. `value=None` — `.env` standartiga qaytaradi."""
    if field not in EDITABLE_FIELDS:
        raise ValueError(f"Noma'lum umumiy limit maydoni: {field!r}")

    stmt = (
        pg_insert(SystemSettings)
        .values(id=1, **{field: value})
        .on_conflict_do_update(index_elements=[SystemSettings.id], set_={field: value})
    )
    await session.execute(stmt)
    invalidate_cache()
