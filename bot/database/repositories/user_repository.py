from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot import locales
from bot.config.settings import settings
from bot.database.models import User, UserSettings
from bot.services.events import utcnow


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── O'qish ────────────────────────────────────────────────

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        result = await self.session.execute(
            select(User)
            .options(selectinload(User.settings))
            .where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: int) -> Optional[User]:
        result = await self.session.execute(
            select(User).options(selectinload(User.settings)).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def find_by_username(self, username: str, *, limit: int = 10) -> List[User]:
        """Username bo'yicha qidiradi (@ va registr farqsiz)."""
        cleaned = username.strip().lstrip("@").lower()
        if not cleaned:
            return []
        result = await self.session.execute(
            select(User)
            .options(selectinload(User.settings))
            .where(func.lower(User.username) == cleaned)
            .order_by(User.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    # ── Yozish ────────────────────────────────────────────────

    async def get_or_create(
        self,
        telegram_id: int,
        *,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        telegram_lang: Optional[str] = None,
        is_premium: bool = False,
        source: Optional[str] = None,
        referred_by: Optional[int] = None,
    ) -> Tuple[User, bool]:
        """Userni topadi yoki yaratadi. `(user, yangi_yaratildimi)` qaytaradi.

        Bu metod har bir kelgan xabarda chaqiriladi, shuning uchun issiq yo'l
        (mavjud user) bitta SELECT ga tushirilgan. Profil maydonlari faqat
        haqiqatan o'zgarganda yoziladi — username kamdan-kam o'zgaradi, har
        xabarda UPDATE qilish behuda yozuv yuki.
        """
        user = await self.get_by_telegram_id(telegram_id)

        if user is not None:
            changes = {}
            if user.username != username:
                changes["username"] = username
            if user.first_name != first_name:
                changes["first_name"] = first_name
            if user.last_name != last_name:
                changes["last_name"] = last_name
            if telegram_lang and user.telegram_lang != telegram_lang:
                changes["telegram_lang"] = telegram_lang
            if user.is_premium != is_premium:
                changes["is_premium"] = is_premium

            now = utcnow()
            # `last_seen_at` ni har xabarda emas, daqiqada bir marta yangilaymiz.
            if user.last_seen_at is None or (now - user.last_seen_at) > timedelta(seconds=60):
                changes["last_seen_at"] = now

            # Botni bloklagan user qaytib kelsa — holatini tiklaymiz.
            if user.status == "blocked_bot":
                changes["status"] = "active"
                changes["blocked_at"] = None

            if changes:
                await self.session.execute(
                    update(User).where(User.id == user.id).values(**changes)
                )
                for key, value in changes.items():
                    setattr(user, key, value)

            if user.settings is None:
                await self._ensure_settings(user.id, telegram_lang)
                user = await self.get_by_id(user.id)
            elif telegram_lang:
                # Interfeys tili avtomatik — Telegram tili o'zgarsa ko'chirmani
                # ham yangilaymiz. `interface_lang` xabar tarqatish va
                # statistika uchun kerak; eskirgan qiymat noto'g'ri tildagi
                # xabar yuborilishiga olib kelardi.
                resolved = locales.resolve(telegram_lang)
                if user.settings.interface_lang != resolved:
                    await self.session.execute(
                        update(UserSettings)
                        .where(UserSettings.user_id == user.id)
                        .values(interface_lang=resolved)
                    )
                    user.settings.interface_lang = resolved

            return user, False

        # Yangi user. Poyga holatida (parallel xabarlar) ON CONFLICT saqlaydi.
        stmt = (
            pg_insert(User)
            .values(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                telegram_lang=telegram_lang,
                is_premium=is_premium,
                source=source,
                referred_by=referred_by,
                last_seen_at=utcnow(),
            )
            .on_conflict_do_nothing(index_elements=[User.telegram_id])
            .returning(User.id)
        )
        inserted_id = (await self.session.execute(stmt)).scalar_one_or_none()

        if inserted_id is None:
            # Boshqa so'rov bizdan oldin ulgurdi — bu yangi user emas.
            user = await self.get_by_telegram_id(telegram_id)
            return user, False

        await self._ensure_settings(inserted_id, telegram_lang)
        return await self.get_by_id(inserted_id), True

    async def _ensure_settings(self, user_id: int, telegram_lang: Optional[str]) -> None:
        # Interfeys tili Telegram tilidan aniqlanadi: `uz` → o'zbekcha,
        # `id` → indonez, qolgani → ingliz. Xom `telegram_lang[:2]` yozib
        # bo'lmaydi — u qo'llab-quvvatlanmaydigan kod berardi (`ru`, `fr`).
        await self.session.execute(
            pg_insert(UserSettings)
            .values(
                user_id=user_id,
                source_lang=settings.DEFAULT_SOURCE_LANG,
                target_lang=settings.DEFAULT_TARGET_LANG,
                interface_lang=locales.resolve(telegram_lang),
            )
            .on_conflict_do_nothing(index_elements=[UserSettings.user_id])
        )

    async def touch(self, user_id: int) -> None:
        await self.session.execute(
            update(User).where(User.id == user_id).values(last_seen_at=utcnow())
        )

    async def mark_blocked(self, user_id: int) -> None:
        await self.session.execute(
            update(User)
            .where(User.id == user_id)
            .values(status="blocked_bot", blocked_at=utcnow())
        )

    async def mark_unreachable(self, user_id: int) -> None:
        """Chat umuman mavjud emas — `deleted` holatiga o'tkazadi.

        `blocked_bot` dan farqi: bloklagan odam botni qayta ochsa holat
        tiklanadi, bu esa qaytmaydi (akkaunt o'chirilgan, id buzuq yoki
        nishon o'zi bot). `deleted` `BROADCAST_STATUSES` da yo'q, ya'ni
        bunday yozuvlar keyingi tarqatishlarga umuman tushmaydi.
        """
        await self.session.execute(
            update(User).where(User.id == user_id).values(status="deleted")
        )

    async def mark_many_unblocked(self, user_ids: List[int]) -> int:
        """Xabar yetib borgan `blocked_bot` userlarni `active` ga qaytaradi.

        Tarqatish paytida chaqiriladi: yuborish muvaffaqiyatli bo'lsa, odam
        botni blokdan chiqargan degani. Telegram bu haqda xabar bermaydi,
        shuning uchun buni bilishning yagona yo'li — yuborib ko'rish.

        `status = 'blocked_bot'` sharti muhim: allaqachon `active` bo'lganlar
        uchun bu hech narsa qilmaydi, ya'ni har bir muvaffaqiyat uchun
        chaqirish xavfsiz.
        """
        if not user_ids:
            return 0
        result = await self.session.execute(
            update(User)
            .where(User.id.in_(user_ids), User.status == "blocked_bot")
            .values(status="active", blocked_at=None)
        )
        return result.rowcount or 0

    async def mark_unblocked(self, user_id: int) -> None:
        await self.session.execute(
            update(User)
            .where(User.id == user_id, User.status == "blocked_bot")
            .values(status="active", blocked_at=None)
        )

    async def set_role(self, user_id: int, role: str) -> None:
        await self.session.execute(update(User).where(User.id == user_id).values(role=role))

    async def set_status(self, user_id: int, status: str) -> None:
        """Foydalanuvchi holatini o'zgartiradi (`active`, `banned`, …)."""
        values: dict = {"status": status}
        if status == "banned":
            values["blocked_at"] = utcnow()
        elif status == "active":
            values["blocked_at"] = None
        await self.session.execute(
            update(User).where(User.id == user_id).values(**values)
        )

    async def set_daily_limit_override(
        self, user_id: int, limit: Optional[int]
    ) -> None:
        """Kunlik tarjima limitini alohida belgilaydi.

        `None` — global standartga qaytaradi.
        `0` yoki manfiy — cheksiz (QuotaService shunday talqin qiladi).
        """
        await self.session.execute(
            update(UserSettings)
            .where(UserSettings.user_id == user_id)
            .values(daily_limit_override=limit)
        )

    async def set_tts_limit_override(
        self, user_id: int, limit: Optional[int]
    ) -> None:
        """`set_daily_limit_override` bilan bir xil mantiq, ovoz (TTS) uchun."""
        await self.session.execute(
            update(UserSettings)
            .where(UserSettings.user_id == user_id)
            .values(tts_limit_override=limit)
        )

    async def set_image_limit_override(
        self, user_id: int, limit: Optional[int]
    ) -> None:
        """`set_daily_limit_override` bilan bir xil mantiq, rasm (OCR) uchun."""
        await self.session.execute(
            update(UserSettings)
            .where(UserSettings.user_id == user_id)
            .values(image_limit_override=limit)
        )

    async def extend_premium(
        self, user_id: int, days: int, *, max_days: int
    ) -> Optional[datetime]:
        """VIP muddatini uzaytiradi (stacking) va yakuniy sanani qaytaradi.

        Stacking: agar VIP hali faol bo'lsa, kunlar UNING ustiga qo'shiladi
        (hozirgi vaqtga emas) — ikkinchi homiylik birinchisini "yeb qo'ymaydi".
        `max_days` bilan kesiladi: bitta uzaytirish hozirgi vaqtdan shu
        chegaradan uzoqqa cho'zilmaydi (himoya — juda katta yagona homiylik
        cheksiz uzoq VIP bermasin).

        `days <= 0` bo'lsa hech narsa qilinmaydi (masalan kichik homiylik
        bir kunlik VIP'ga yetmasa) — `None` qaytadi.
        """
        if days <= 0:
            return None

        result = await self.session.execute(
            select(UserSettings.premium_until).where(UserSettings.user_id == user_id)
        )
        current = result.scalar_one_or_none()

        now = utcnow()
        base = current if current is not None and current > now else now
        new_until = min(base + timedelta(days=days), now + timedelta(days=max_days))

        await self.session.execute(
            update(UserSettings)
            .where(UserSettings.user_id == user_id)
            .values(premium_until=new_until)
        )
        return new_until

    async def set_referral_bonus_granted(self, user_id: int) -> None:
        await self.session.execute(
            update(UserSettings)
            .where(UserSettings.user_id == user_id)
            .values(referral_bonus_granted=True)
        )

    # ── Statistika ────────────────────────────────────────────

    async def count_referrals(self, user_id: int) -> int:
        return (
            await self.session.execute(
                select(func.count(User.id)).where(User.referred_by == user_id)
            )
        ).scalar_one()

    async def count_total(self) -> int:
        return (await self.session.execute(select(func.count(User.id)))).scalar_one()

    async def count_by_status(self) -> dict[str, int]:
        rows = await self.session.execute(
            select(User.status, func.count(User.id)).group_by(User.status)
        )
        return {status: count for status, count in rows.all()}

    async def count_active_since(self, days: int = 7) -> int:
        cutoff = utcnow() - timedelta(days=days)
        return (
            await self.session.execute(
                select(func.count(User.id)).where(User.last_seen_at >= cutoff)
            )
        ).scalar_one()

    async def count_new_since(self, days: int = 1) -> int:
        cutoff = utcnow() - timedelta(days=days)
        return (
            await self.session.execute(
                select(func.count(User.id)).where(User.created_at >= cutoff)
            )
        ).scalar_one()

    # Xabar tarqatishga kiradigan holatlar.
    #
    # `blocked_bot` ataylab KIRADI. Bloklash qaytariladigan holat: odam botni
    # blokdan chiqarishi mumkin va Telegram bu haqda xabar bermaydi — buni
    # bilishning yagona yo'li yuborib ko'rish. Yuborish muvaffaqiyatli
    # bo'lsa, o'sha yerda `active` ga qaytariladi.
    #
    # Kirmaydiganlar:
    #   `deleted` — chat umuman mavjud emas (o'chirilgan akkaunt, buzuq id,
    #               nishon o'zi bot). Hech qachon yetmaydi, urinish behuda.
    #   `banned`  — biz o'zimiz taqiqlaganmiz.
    BROADCAST_STATUSES = ("active", "blocked_bot")

    async def count_broadcast_targets(self, exclude_user_id: Optional[int] = None) -> int:
        """Taxminiy jami — tarqatish davomida yangi userlar qo'shilgani uchun
        aniq son emas, faqat boshlang'ich baho (progress % shuning uchun
        davriy ravishda qayta hisoblanadi)."""
        query = select(func.count(User.id)).where(User.status.in_(self.BROADCAST_STATUSES))
        if exclude_user_id is not None:
            query = query.where(User.id != exclude_user_id)
        return (await self.session.execute(query)).scalar_one()

    async def fetch_broadcast_batch(
        self,
        *,
        after_id: Optional[int],
        limit: int,
        exclude_user_id: Optional[int] = None,
    ) -> List[Tuple[int, int]]:
        """`(user_id, telegram_id)` — `after_id` dan keyingi, `id` bo'yicha.

        `users.id` — avtomatik ortib boruvchi PK, ya'ni tabiiy kursor:
        tarqatish davomida qo'shilgan yangi user doim `after_id`dan katta
        bo'ladi va keyingi chaqiruvda o'zi qamrab olinadi — alohida
        "yangi userlarni sinxronlash" bosqichi kerak emas.
        """
        query = select(User.id, User.telegram_id).where(
            User.status.in_(self.BROADCAST_STATUSES)
        )
        if after_id is not None:
            query = query.where(User.id > after_id)
        if exclude_user_id is not None:
            query = query.where(User.id != exclude_user_id)
        result = await self.session.execute(query.order_by(User.id).limit(limit))
        return [(row[0], row[1]) for row in result.all()]
