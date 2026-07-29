from datetime import timedelta
from typing import List, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import Translation, TranslationSignal
from bot.services.events import utcnow


class TranslationRepository:
    """`translations` — append-only. UPDATE metodlari ataylab yo'q.

    Sevimli holati ham shu yerda emas, `translation_signals` da saqlanadi:
    "foydalanuvchi sevimliga qo'shdi" — bu vaqt bilan bog'liq voqea, holat emas.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **fields) -> Translation:
        translation = Translation(**fields)
        self.session.add(translation)
        # PK kerak (tugmalar callback_data'siga yoziladi), shuning uchun flush.
        await self.session.flush()
        return translation

    async def get(self, translation_id: int) -> Optional[Translation]:
        return await self.session.get(Translation, translation_id)

    async def history(
        self, user_id: int, *, limit: int = 10, offset: int = 0
    ) -> Sequence[Translation]:
        result = await self.session.execute(
            select(Translation)
            .where(Translation.user_id == user_id, Translation.status == "success")
            .order_by(Translation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def history_count(self, user_id: int) -> int:
        return (
            await self.session.execute(
                select(func.count(Translation.id)).where(
                    Translation.user_id == user_id, Translation.status == "success"
                )
            )
        ).scalar_one()

    async def favorites(self, user_id: int, *, limit: int = 10, offset: int = 0):
        """Sevimlilar = oxirgi signali `favorited` bo'lgan tarjimalar.

        `unfavorited` signali ham yozilgani uchun DISTINCT ON bilan har bir
        tarjima uchun eng oxirgi signalni olamiz.
        """
        latest = (
            select(
                TranslationSignal.translation_id,
                TranslationSignal.signal,
                func.row_number()
                .over(
                    partition_by=TranslationSignal.translation_id,
                    order_by=TranslationSignal.created_at.desc(),
                )
                .label("rn"),
            )
            .where(
                TranslationSignal.user_id == user_id,
                TranslationSignal.signal.in_(("favorited", "unfavorited")),
            )
            .subquery()
        )

        result = await self.session.execute(
            select(Translation)
            .join(latest, latest.c.translation_id == Translation.id)
            .where(latest.c.rn == 1, latest.c.signal == "favorited")
            .order_by(Translation.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return result.scalars().all()

    async def is_favorite(self, user_id: int, translation_id: int) -> bool:
        result = await self.session.execute(
            select(TranslationSignal.signal)
            .where(
                TranslationSignal.user_id == user_id,
                TranslationSignal.translation_id == translation_id,
                TranslationSignal.signal.in_(("favorited", "unfavorited")),
            )
            .order_by(TranslationSignal.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none() == "favorited"

    async def add_signal(
        self,
        *,
        translation_id: int,
        user_id: Optional[int],
        signal: str,
        correction: Optional[str] = None,
    ) -> None:
        self.session.add(
            TranslationSignal(
                translation_id=translation_id,
                user_id=user_id,
                signal=signal,
                correction=correction,
            )
        )

    async def find_recent_by_hash(
        self, user_id: int, source_hash: str, *, within_seconds: int = 60
    ) -> Optional[Translation]:
        """Yaqinda shu matn tarjima qilinganmi — `retranslated` signalini aniqlash uchun."""
        result = await self.session.execute(
            select(Translation)
            .where(
                Translation.user_id == user_id,
                Translation.source_hash == source_hash,
                Translation.created_at >= utcnow() - timedelta(seconds=within_seconds),
            )
            .order_by(Translation.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    # ── Statistika ────────────────────────────────────────────

    async def count_total(self) -> int:
        return (await self.session.execute(select(func.count(Translation.id)))).scalar_one()

    async def count_since(self, days: int = 1) -> int:
        return (
            await self.session.execute(
                select(func.count(Translation.id)).where(
                    Translation.created_at >= utcnow() - timedelta(days=days)
                )
            )
        ).scalar_one()

    async def top_language_pairs(self, *, limit: int = 10) -> List[tuple]:
        result = await self.session.execute(
            select(
                Translation.source_lang_detected,
                Translation.target_lang,
                func.count(Translation.id).label("n"),
            )
            .where(Translation.status == "success")
            .group_by(Translation.source_lang_detected, Translation.target_lang)
            .order_by(func.count(Translation.id).desc())
            .limit(limit)
        )
        return [tuple(row) for row in result.all()]

    async def error_rate_since(self, days: int = 1) -> tuple[int, int]:
        """`(xatolar, jami)` — provayder sog'lig'ini kuzatish uchun."""
        cutoff = utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(
                func.count(Translation.id).filter(Translation.status != "success"),
                func.count(Translation.id),
            ).where(Translation.created_at >= cutoff)
        )
        errors, total = result.one()
        return errors or 0, total or 0
