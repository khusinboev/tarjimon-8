"""Kunlik limit va spam himoyasi.

To'rt xil cheklov, to'rt xil maqsad:
  - **Kunlik limit** (matn, ovoz, rasm) — resurs sarfini cheklaydi. Haqiqat
    manbai — `daily_usage` jadvali, Redis faqat tezlashtiruvchi kesh. Standart
    qiymatlar `system_config.py` orqali keladi — admin panelidan sozlanadi,
    sozlanmagan bo'lsa `.env` dagi qiymat ishlatiladi.
  - **Rate limit** (60 soniyada 20 so'rov) — flood'dan himoya. Faqat Redis'da,
    chunki yo'qolsa hech narsa buzilmaydi.
  - **VIP muddati** (`premium_until`) — homiylik yoki referal orqali qo'lga
    kiritiladi. Matn va ovoz shu muddat ichida CHEKSIZ, rasm esa faqat
    KENGAYTIRILGAN (masalan 3 → 15/kun) — OCR tashqi provayderga pul/hajm
    sarflagani uchun uni cheksiz qilib bo'lmaydi.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.repositories.usage_repository import UsageRepository
from bot.services.events import utcnow
from bot.services.system_config import get_effective_limits

if TYPE_CHECKING:
    from bot.database.models import User


@dataclass(slots=True)
class QuotaStatus:
    allowed: bool
    used: int
    limit: int

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)


def resolve_limit_override(
    user: "User",
    *,
    kind: Literal["translation", "tts", "image"],
    vip_value: int = 0,
) -> Optional[int]:
    """Foydalanuvchi uchun qo'llanadigan limit-override'ni hisoblaydi.

    Ustunlik tartibi (yuqoridan pastga, birinchi topilgani g'olib):
      1. Admin/owner roli       — har doim cheksiz (0).
      2. Admin qo'ygan override — `daily_limit_override` / `tts_limit_override` /
         `image_limit_override`. Bu ANIQ qaror, hatto VIP muddati tugagan
         bo'lsa ham ustun turadi.
      3. Faol VIP (`premium_until` kelajakda) — `vip_value` qaytadi.
         Matn/ovoz uchun chaqiruvchi `0` (cheksiz) beradi, rasm uchun esa
         kengaytirilgan sonli limit (masalan 15) — OCR pulga tushgani uchun
         VIP'da ham cheksiz emas.
      4. Hech biri yo'q — `None`, ya'ni chaqiruvchi global standartni qo'llaydi.

    `None` qaytarilishi "override yo'q" degani, `check_translation`/`check_tts`/
    `check_image` buni global standart bilan almashtiradi — bu bilan `0`
    (haqiqiy cheksiz) va "override yo'q" holatlari chalkashmaydi (avvalgi
    xato aynan shu farqni Python'ning `or` operatori bilan yo'qotgan edi).
    """
    if user.role in ("admin", "owner"):
        return 0

    settings_row = user.settings
    if settings_row is None:
        return None

    if kind == "translation":
        manual = settings_row.daily_limit_override
    elif kind == "tts":
        manual = settings_row.tts_limit_override
    else:
        manual = settings_row.image_limit_override
    if manual is not None:
        return manual

    premium_until = settings_row.premium_until
    if premium_until is not None and premium_until > utcnow():
        return vip_value

    return None


class QuotaService:
    def __init__(self, session: AsyncSession, redis=None):
        self.session = session
        self.usage = UsageRepository(session)
        self.redis = redis

    async def check_translation(
        self, user_id: int, *, limit_override: Optional[int] = None
    ) -> QuotaStatus:
        # `is not None` shart: `0 or DEFAULT` Python'da `0` yolg'on qiymat
        # bo'lgani uchun har doim `DEFAULT` ga tushib qolardi va "0 — cheksiz"
        # degan shartnoma (adminlar, `set_user_limit(..., 0)`) buzilardi.
        if limit_override is not None:
            limit = limit_override
        else:
            limit = (await get_effective_limits(self.session)).translation
        # 0 yoki manfiy — cheksiz (adminlar, VIP va maxsus userlar uchun).
        if limit <= 0:
            return QuotaStatus(allowed=True, used=0, limit=0)

        row = await self.usage.get(user_id)
        used = row.translations_count if row else 0
        return QuotaStatus(allowed=used < limit, used=used, limit=limit)

    async def check_tts(
        self, user_id: int, *, limit_override: Optional[int] = None
    ) -> QuotaStatus:
        # `check_translation` bilan bir xil mantiq — mukammal simmetriya.
        if limit_override is not None:
            limit = limit_override
        else:
            limit = (await get_effective_limits(self.session)).tts
        if limit <= 0:
            return QuotaStatus(allowed=True, used=0, limit=0)

        row = await self.usage.get(user_id)
        used = row.tts_count if row else 0
        return QuotaStatus(allowed=used < limit, used=used, limit=limit)

    async def check_image(
        self, user_id: int, *, limit_override: Optional[int] = None
    ) -> QuotaStatus:
        """`check_translation`/`check_tts` bilan bir xil mantiq, rasm uchun.

        `limit_override` — odatda `resolve_limit_override(user, kind="image",
        vip_value=...)` natijasi: admin/qo'lda/VIP holatlarida allaqachon
        aniq son, faqat ikkalasi ham yo'qligida `None` (standart — bepul
        foydalanuvchi limiti) qoladi.
        """
        if limit_override is not None:
            limit = limit_override
        else:
            limit = (await get_effective_limits(self.session)).image_free
        if limit <= 0:
            return QuotaStatus(allowed=True, used=0, limit=0)

        row = await self.usage.get(user_id)
        used = row.images_count if row else 0
        return QuotaStatus(allowed=used < limit, used=used, limit=limit)

    async def consume_translation(self, user_id: int, chars: int = 0) -> None:
        await self.usage.increment(user_id, translations=1, chars=chars)

    async def consume_tts(self, user_id: int) -> None:
        await self.usage.increment(user_id, tts=1)

    async def consume_image(self, user_id: int) -> None:
        await self.usage.increment(user_id, images=1)

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
