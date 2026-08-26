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
from bot.database.redis import atomic_rate_incr
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


_UNSET = object()


def resolve_limit_override(
    user: "User",
    *,
    kind: Literal["translation", "tts", "image"],
    vip_value: int = _UNSET,  # type: ignore[assignment]
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

    `None` qaytarilishi "override yo'q" degani, `try_reserve_translation`/
    `try_reserve_tts`/`try_reserve_image` buni global standart bilan
    almashtiradi — bu bilan `0`
    (haqiqiy cheksiz) va "override yo'q" holatlari chalkashmaydi (avvalgi
    xato aynan shu farqni Python'ning `or` operatori bilan yo'qotgan edi).

    `vip_value` uchun standart QIYMAT ATAYLAB YO'Q (majburiy): `kind="image"`
    uchun uni unutib qoldirish (masalan yangi chaqiruv nuqtasi qo'shilganda)
    OCR'ni — pulga tushadigan tashqi provayderni — VIP'da CHEKSIZ qilib
    qo'yardi, bu esa siyosatga zid (yuqoridagi izohga qarang). `translation`/
    `tts` uchun `0` (cheksiz) har doim to'g'ri bo'lgani uchun ular ataylab
    aniq `vip_value=0` yozadi — bu ikkalasi ham qasddan, tasodifiy emas.
    """
    if vip_value is _UNSET:
        raise TypeError(
            "resolve_limit_override(): `vip_value` majburiy — 'image' uchun "
            "tasodifan cheksiz bo'lib qolmasin deb standart qiymat yo'q "
            "(translation/tts uchun ham aniq vip_value=0 bering)."
        )

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

    async def _resolve_limit(self, kind: str, limit_override: Optional[int]) -> int:
        if limit_override is not None:
            return limit_override
        limits = await get_effective_limits(self.session)
        return {"translation": limits.translation, "tts": limits.tts, "image": limits.image_free}[kind]

    async def try_reserve_translation(
        self, user_id: int, *, chars: int = 0, limit_override: Optional[int] = None
    ) -> QuotaStatus:
        """Kvotani ATOMIK band qiladi (tekshirish + oshirish bitta SQL
        amaliyotida — TOCTOU yo'q, izoh: `UsageRepository.try_reserve`).

        `allowed=True` bo'lsa — joy band qilingan, chaqiruvchi tashqi
        so'rovni (tarjima) davom ettirishi mumkin. Agar o'sha so'rov
        keyinchalik MUVAFFAQIYATSIZ bo'lsa — `release_translation()`
        bilan ortga qaytarilishi SHART, aks holda muvaffaqiyatsiz urinish
        ham foydalanuvchining kvotasidan yeb qo'yadi.
        """
        # `is not None` shart: `0 or DEFAULT` Python'da `0` yolg'on qiymat
        # bo'lgani uchun har doim `DEFAULT` ga tushib qolardi va "0 — cheksiz"
        # degan shartnoma (adminlar, `set_user_limit(..., 0)`) buzilardi.
        limit = await self._resolve_limit("translation", limit_override)
        if limit <= 0:
            # Cheksiz bo'lsa ham hisob yuritiladi (statistika/admin karta
            # uchun) — faqat GATE qilinmaydi. `release_translation()` bu
            # holatda ham to'g'ri ishlaydi (xuddi shu +1 ni qaytaradi).
            # Qaytgan qatordan haqiqiy `used`ni o'qiymiz — avval doim `0`
            # yozilardi, garchi haqiqiy hisob yuritilsa ham (chalg'ituvchi
            # edi, masalan kelajakda "bugun ishlatilgan" ko'rsatsa).
            row = await self.usage.increment(user_id, translations=1, chars=chars)
            return QuotaStatus(allowed=True, used=row.translations_count, limit=0)

        reserved = await self.usage.try_reserve(user_id, "translations_count", limit, chars=chars)
        row = await self.usage.get(user_id)
        used = row.translations_count if row else (1 if reserved else 0)
        return QuotaStatus(allowed=reserved, used=used, limit=limit)

    async def try_reserve_tts(
        self, user_id: int, *, limit_override: Optional[int] = None
    ) -> QuotaStatus:
        """`try_reserve_translation` bilan bir xil mantiq — mukammal simmetriya."""
        limit = await self._resolve_limit("tts", limit_override)
        if limit <= 0:
            row = await self.usage.increment(user_id, tts=1)
            return QuotaStatus(allowed=True, used=row.tts_count, limit=0)

        reserved = await self.usage.try_reserve(user_id, "tts_count", limit)
        row = await self.usage.get(user_id)
        used = row.tts_count if row else (1 if reserved else 0)
        return QuotaStatus(allowed=reserved, used=used, limit=limit)

    async def try_reserve_image(
        self, user_id: int, *, limit_override: Optional[int] = None
    ) -> QuotaStatus:
        """`try_reserve_translation`/`try_reserve_tts` bilan bir xil mantiq, rasm uchun.

        `limit_override` — odatda `resolve_limit_override(user, kind="image",
        vip_value=...)` natijasi: admin/qo'lda/VIP holatlarida allaqachon
        aniq son, faqat ikkalasi ham yo'qligida `None` (standart — bepul
        foydalanuvchi limiti) qoladi.
        """
        limit = await self._resolve_limit("image", limit_override)
        if limit <= 0:
            row = await self.usage.increment(user_id, images=1)
            return QuotaStatus(allowed=True, used=row.images_count, limit=0)

        reserved = await self.usage.try_reserve(user_id, "images_count", limit)
        row = await self.usage.get(user_id)
        used = row.images_count if row else (1 if reserved else 0)
        return QuotaStatus(allowed=reserved, used=used, limit=limit)

    async def release_translation(self, user_id: int, chars: int = 0) -> None:
        await self.usage.release(user_id, "translations_count", chars=chars)

    async def release_tts(self, user_id: int) -> None:
        await self.usage.release(user_id, "tts_count")

    async def release_image(self, user_id: int) -> None:
        await self.usage.release(user_id, "images_count")

    async def hit_rate_limit(self, user_id: int) -> bool:
        """Flood himoyasi. Redis yo'q bo'lsa — o'tkazib yuboradi (fail-open).

        Fail-open ataylab: Redis tushib qolganda butun bot to'xtab qolgandan
        ko'ra, spam himoyasi vaqtincha ishlamagani afzal.
        """
        if not self.redis:
            return False
        try:
            key = f"rl:{user_id}"
            # Atomik Lua skript orqali — alohida incr+expire orasida
            # uzilib qolsa, kalit TTL'siz abadiy o'sib, foydalanuvchini
            # doimiy bloklab qo'yishi mumkin edi (izoh: `atomic_rate_incr`).
            count = await atomic_rate_incr(self.redis, key, settings.RATE_LIMIT_WINDOW)
            return count > settings.RATE_LIMIT_REQUESTS
        except Exception:
            return False
