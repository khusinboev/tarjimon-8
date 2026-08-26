from datetime import date as date_type
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import DailyUsage
from bot.services.events import utcnow

_COUNT_FIELDS = ("translations_count", "tts_count", "images_count")


class UsageRepository:
    """Kunlik limit hisobi.

    `events` yoki `translations` dan COUNT(*) qilish har bir xabarda katta jadvalni
    skanlashni anglatadi. Bu jadval — bitta qator/user/kun, arzon UPSERT.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def today() -> date_type:
        # Limit UTC kuni bo'yicha — server va bazada bir xil mantiq.
        return utcnow().date()

    async def get(self, user_id: int, day: Optional[date_type] = None) -> Optional[DailyUsage]:
        result = await self.session.execute(
            select(DailyUsage).where(
                DailyUsage.user_id == user_id,
                DailyUsage.date == (day or self.today()),
            )
        )
        return result.scalar_one_or_none()

    async def increment(
        self,
        user_id: int,
        *,
        translations: int = 0,
        tts: int = 0,
        images: int = 0,
        chars: int = 0,
        day: Optional[date_type] = None,
    ) -> DailyUsage:
        """Atomik UPSERT — parallel xabarlar hisobni buzmasligi uchun."""
        day = day or self.today()
        stmt = (
            pg_insert(DailyUsage)
            .values(
                user_id=user_id,
                date=day,
                translations_count=translations,
                tts_count=tts,
                images_count=images,
                chars_count=chars,
            )
            .on_conflict_do_update(
                index_elements=[DailyUsage.user_id, DailyUsage.date],
                set_={
                    "translations_count": DailyUsage.translations_count + translations,
                    "tts_count": DailyUsage.tts_count + tts,
                    "images_count": DailyUsage.images_count + images,
                    "chars_count": DailyUsage.chars_count + chars,
                    "updated_at": utcnow(),
                },
            )
            .returning(DailyUsage)
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def try_reserve(
        self, user_id: int, field: str, limit: int, *, chars: int = 0, day: Optional[date_type] = None,
    ) -> bool:
        """Atomik "faqat limitdan hali oshmagan bo'lsa +1 qil" — TOCTOU'siz.

        Oddiy "avval tekshir (`check_*`), keyin — sekin tashqi chaqiruvdan
        SO'NG — oshir (`increment`)" naqshida ikkita deyarli bir vaqtdagi
        so'rov (masalan tez-tez ikki marta bosish yoki skript) ikkalasi
        ham "hali limitdan oshmagan" deb ko'rishi va ikkalasi ham
        xizmatdan foydalanishi mumkin edi — limit shu qadar chetlab
        o'tilardi. Bu metod tekshirish VA oshirishni BITTA atomik SQL
        amaliyotiga birlashtiradi: Postgres qatorni UPDATE paytida
        qulflaydi, ikkinchi chaqiruv birinchisi tugagunicha kutadi va
        keyin YANGILANGAN qiymatni ko'radi.

        `field` — "translations_count" | "tts_count" | "images_count".
        Chaqiruvchi keyin tashqi so'rov (tarjima/OCR/TTS) MUVAFFAQIYATSIZ
        bo'lsa, `release()` bilan ortga qaytarishi kerak — aks holda
        muvaffaqiyatsiz urinish ham kvotadan yeb qo'yadi (avvalgi
        xulq-atvor: faqat muvaffaqiyatli urinish sarflanardi).
        """
        assert field in _COUNT_FIELDS
        day = day or self.today()
        count_col = getattr(DailyUsage, field)
        stmt = (
            pg_insert(DailyUsage)
            .values(user_id=user_id, date=day, **{field: 1}, chars_count=chars)
            .on_conflict_do_update(
                index_elements=[DailyUsage.user_id, DailyUsage.date],
                set_={
                    field: count_col + 1,
                    "chars_count": DailyUsage.chars_count + chars,
                    "updated_at": utcnow(),
                },
                where=(count_col < limit),
            )
            .returning(DailyUsage.user_id)
        )
        result = await self.session.execute(stmt)
        return result.first() is not None

    async def release(
        self, user_id: int, field: str, *, chars: int = 0, day: Optional[date_type] = None,
    ) -> None:
        """`try_reserve()`ni ortga qaytaradi — tashqi chaqiruv muvaffaqiyatsiz
        bo'lganda. `GREATEST(0, ...)` — hech qachon manfiyga tushmasin."""
        assert field in _COUNT_FIELDS
        day = day or self.today()
        count_col = getattr(DailyUsage, field)
        await self.session.execute(
            pg_insert(DailyUsage)
            .values(user_id=user_id, date=day)
            .on_conflict_do_update(
                index_elements=[DailyUsage.user_id, DailyUsage.date],
                set_={
                    field: func.greatest(0, count_col - 1),
                    "chars_count": func.greatest(0, DailyUsage.chars_count - chars),
                    "updated_at": utcnow(),
                },
            )
        )
