from datetime import timedelta
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

    async def mark_unblocked(self, user_id: int) -> None:
        await self.session.execute(
            update(User)
            .where(User.id == user_id, User.status == "blocked_bot")
            .values(status="active", blocked_at=None)
        )

    async def set_role(self, user_id: int, role: str) -> None:
        await self.session.execute(update(User).where(User.id == user_id).values(role=role))

    # ── Statistika ────────────────────────────────────────────

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

    async def iter_broadcast_targets(
        self, exclude_user_id: Optional[int] = None
    ) -> List[Tuple[int, int]]:
        """Xabar tarqatish uchun `(user_id, telegram_id)` ro'yxati.

        Faqat `active` — botni bloklaganlarga yuborish Telegram limitini behuda sarflaydi.
        """
        query = select(User.id, User.telegram_id).where(User.status == "active")
        if exclude_user_id is not None:
            query = query.where(User.id != exclude_user_id)
        result = await self.session.execute(query.order_by(User.id))
        return [(row[0], row[1]) for row in result.all()]
