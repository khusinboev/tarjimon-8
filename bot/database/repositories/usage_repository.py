from datetime import date as date_type
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import DailyUsage
from bot.services.events import utcnow


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
                chars_count=chars,
            )
            .on_conflict_do_update(
                index_elements=[DailyUsage.user_id, DailyUsage.date],
                set_={
                    "translations_count": DailyUsage.translations_count + translations,
                    "tts_count": DailyUsage.tts_count + tts,
                    "chars_count": DailyUsage.chars_count + chars,
                    "updated_at": utcnow(),
                },
            )
            .returning(DailyUsage)
        )
        return (await self.session.execute(stmt)).scalar_one()
