from datetime import date as date_type
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.util import identity_key

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

    def _expire_cached_row(self, user_id: int, day: date_type) -> None:
        """Faqat AYNAN shu (user_id, day) uchun keshlangan `DailyUsage`
        obyektini (agar bor bo'lsa) eskirgan deb belgilaydi.

        `session.expire_all()` EMAS — u BUTUN sessiyadagi hamma obyektni
        (masalan shu so'rovda allaqachon yuklangan `User`/`UserSettings`)
        ham eskirtirib qo'yardi. Keyin ularning oddiy atributiga (masalan
        `user.id`) sinxron kirish yashirin qayta yuklashga urinib,
        `MissingGreenlet` xatosi bilan BUTUN so'rovni yiqitardi — bu
        production'da haqiqatan sodir bo'ldi (qo'lda tuzatilgan, saboq
        sifatida qoldirilmoqda: hech qachon `expire_all()`ni maqsadli
        `expire(instance)` o'rniga ishlatmang).
        """
        key = identity_key(DailyUsage, (user_id, day))
        instance = self.session.identity_map.get(key)
        if instance is not None:
            self.session.expire(instance)

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
        """Atomik UPSERT — parallel xabarlar hisobni buzmasligi uchun.

        Qaytgan qiymatning o'zi har doim toza (RETURNING'dan to'g'ridan-
        to'g'ri o'qiladi). `try_reserve`/`release`dan farqli — bu yerda
        `expire_all()` ATAYLAB chaqirilmaydi: u qaytarilayotgan obyektning
        o'zini ham "eskirgan" deb belgilab qo'yardi, keyin uning
        atributiga (masalan `.translations_count`) kirish async sessiyada
        yashirin (avtomatik) qayta yuklashga urinib, xatoga olib kelardi.
        Hozircha bu metodning qaytgan qiymatidan hech kim foydalanmaydi —
        agar kelajakda kimdir shu obyektni o'qib, KEYIN yana `get()` bilan
        boshqa joydan o'qisa, o'sha ikkinchi o'qishda eskirish xavfi bor
        (past darajali, `try_reserve`/`release` kabi emas).
        """
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

        Sessiyada shu (user_id, day) uchun avvalroq yuklangan `DailyUsage`
        obyekti bo'lsa — u shu yerdagi xom (dialektga xos `ON CONFLICT`)
        UPSERT'dan keyin ESKI qiymat bilan qolib ketardi (SQLAlchemy 2.0
        ORM-DML RETURNING sinxronizatsiyasi faqat haqiqiy INSERT qilingan,
        conflict bo'lmagan obyektlarni to'ldiradi). Shuning uchun
        `_expire_cached_row()` bilan MAQSADLI ravishda (butun sessiyani
        emas, faqat shu bitta obyektni) eskirtiramiz.
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
        reserved = result.first() is not None
        self._expire_cached_row(user_id, day)
        return reserved

    async def release(
        self, user_id: int, field: str, *, chars: int = 0, day: Optional[date_type] = None,
    ) -> None:
        """`try_reserve()`ni ortga qaytaradi — tashqi chaqiruv muvaffaqiyatsiz
        bo'lganda. `GREATEST(0, ...)` — hech qachon manfiyga tushmasin.

        `_expire_cached_row()` haqida — `try_reserve()`dagi izohga qarang.
        """
        assert field in _COUNT_FIELDS
        day = day or self.today()
        count_col = getattr(DailyUsage, field)
        stmt = (
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
        await self.session.execute(stmt)
        self._expire_cached_row(user_id, day)
