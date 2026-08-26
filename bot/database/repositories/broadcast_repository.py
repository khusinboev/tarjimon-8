from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from bot.database.models import Broadcast, BroadcastDelivery
from bot.services.events import utcnow


class BroadcastRepository:
    """Repository for broadcast operations"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_broadcast(
        self,
        created_by: int,
        mode: str,
        content_preview: str | None,
        total_targets: int,
        src_chat_id: int,
        src_message_id: int,
    ) -> Broadcast | None:
        """`None` — DB darajasidagi "bitta faol tarqatish" cheklovi
        (`ux_broadcasts_single_active`, migratsiya 015) buzilgan: boshqa
        tarqatish orada (deyarli bir vaqtda) allaqachon ishga tushib
        ulgurgan. Chaqiruvchi oldindan `get_active()` bilan tekshiradi,
        lekin ikki tekshiruv orasida RACE bo'lishi mumkin — shu holat
        uchun DB o'zi kafolat beradi, kod esa faqat aniq xato ko'rsatadi.
        """
        broadcast = Broadcast(
            created_by=created_by,
            mode=mode,
            content_preview=content_preview,
            total_targets=total_targets,
            src_chat_id=src_chat_id,
            src_message_id=src_message_id,
            status="running",
            started_at=utcnow(),
        )
        self.session.add(broadcast)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            return None
        await self.session.refresh(broadcast)
        return broadcast

    async def add_delivery(self, broadcast_id: int, user_id: int, status: str, error: str | None = None) -> None:
        delivery = BroadcastDelivery(
            broadcast_id=broadcast_id,
            user_id=user_id,
            status=status,
            error=error,
        )
        self.session.add(delivery)
        # No commit here — caller commits in batches

    async def finish_broadcast(
        self, broadcast_id: int, success_count: int, failed_count: int,
        *, active_seconds: int = 0,
    ) -> None:
        await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                status="completed",
                success_count=success_count,
                failed_count=failed_count,
                active_seconds=active_seconds,
                finished_at=utcnow(),
            )
        )
        await self.session.commit()

    async def fail_broadcast(
        self, broadcast_id: int, failed_count: int, *, error: str | None = None,
        active_seconds: int = 0,
    ) -> None:
        await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                status="failed",
                failed_count=failed_count,
                active_seconds=active_seconds,
                error=error,
                finished_at=utcnow(),
            )
        )
        await self.session.commit()

    async def get_status(self, broadcast_id: int) -> str | None:
        result = await self.session.execute(
            select(Broadcast.status).where(Broadcast.id == broadcast_id)
        )
        return result.scalar_one_or_none()

    async def get(self, broadcast_id: int) -> Broadcast | None:
        return await self.session.get(Broadcast, broadcast_id)

    async def get_active(self) -> Broadcast | None:
        """Hozir ketayotgan yoki pauzadagi yagona tarqatish (bo'lsa).

        Bir vaqtda faqat bitta faol tarqatishga ruxsat beriladi (yangisini
        boshlashdan oldin tekshiriladi) — shuning uchun bu yerda ko'plik
        emas, bitta natija kutiladi.
        """
        result = await self.session.execute(
            select(Broadcast)
            .where(
                Broadcast.status.in_(
                    ("running", "pause_requested", "paused", "cancel_requested")
                )
            )
            .order_by(Broadcast.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def request_cancel(self, broadcast_id: int) -> bool:
        """Ishlab turgan tarqatishni bekor qilishni so'raydi (signal).

        Pauzadagi tarqatishda ishlab turgan vazifa yo'q — signalni ko'radigan
        hech kim yo'q, shuning uchun darhol `cancelled` qilinadi.
        """
        result = await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id, Broadcast.status == "paused")
            .values(status="cancelled", finished_at=utcnow())
        )
        if (result.rowcount or 0) == 0:
            result = await self.session.execute(
                update(Broadcast)
                .where(
                    Broadcast.id == broadcast_id,
                    Broadcast.status.in_(("running", "pause_requested")),
                )
                .values(status="cancel_requested")
            )
        await self.session.commit()
        return (result.rowcount or 0) > 0

    async def request_pause(self, broadcast_id: int) -> bool:
        result = await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id, Broadcast.status == "running")
            .values(status="pause_requested")
        )
        await self.session.commit()
        return (result.rowcount or 0) > 0

    async def mark_paused(
        self, broadcast_id: int, *, cursor_user_id: int | None, active_seconds: int,
        success_count: int, failed_count: int,
    ) -> None:
        """Background vazifa pauza so'rovini ko'rib, o'zini to'xtatganda chaqiradi."""
        await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                status="paused",
                cursor_user_id=cursor_user_id,
                active_seconds=active_seconds,
                success_count=success_count,
                failed_count=failed_count,
            )
        )
        await self.session.commit()

    async def resume(self, broadcast_id: int) -> bool:
        """`paused` -> `running`. Chaqiruvchi shundan keyin YANGI background
        vazifa boshlashi kerak — eskisi pauzada tugagan edi."""
        result = await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id, Broadcast.status == "paused")
            .values(status="running")
        )
        await self.session.commit()
        return (result.rowcount or 0) > 0

    async def checkpoint(
        self, broadcast_id: int, *, cursor_user_id: int | None, active_seconds: int,
        success_count: int, failed_count: int, total_targets: int,
    ) -> None:
        """Davriy oraliq saqlash — jarayon o'lsa ham oxirgi shu nuqtadan
        davom etadi (pauza qilinganda emas, muntazam)."""
        await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                cursor_user_id=cursor_user_id,
                active_seconds=active_seconds,
                success_count=success_count,
                failed_count=failed_count,
                total_targets=total_targets,
            )
        )
        await self.session.commit()

    async def recover_interrupted(self) -> list[int]:
        """Bot ishga tushganda chaqiriladi: `running`/`pause_requested`/
        `cancel_requested` holatidagi qatorlarni yuritayotgan vazifa jarayon
        bilan birga o'lgan — ularni `paused`ga o'tkazadi (kursor saqlanadi,
        admin "▶️ Davom ettirish" bilan qayta boshlaydi).

        `(bo'sh bo'lmasa) o'zgartirilgan ID'lar ro'yxati` — ishga tushish
        logiga yozish uchun.
        """
        result = await self.session.execute(
            update(Broadcast)
            .where(Broadcast.status.in_(("running", "pause_requested", "cancel_requested")))
            .values(status="paused")
            .returning(Broadcast.id)
        )
        ids = [row[0] for row in result.all()]
        await self.session.commit()
        return ids

    async def mark_cancelled(
        self, broadcast_id: int, success_count: int, failed_count: int,
        *, active_seconds: int = 0,
    ) -> None:
        await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                status="cancelled",
                success_count=success_count,
                failed_count=failed_count,
                active_seconds=active_seconds,
                finished_at=utcnow(),
            )
        )
        await self.session.commit()

    async def recent(self, limit: int = 10) -> list[Broadcast]:
        """Tugagan (yoki bekor qilingan) so'nggi tarqatishlar — tarix uchun."""
        result = await self.session.execute(
            select(Broadcast)
            .where(Broadcast.status.in_(("completed", "cancelled", "failed")))
            .order_by(Broadcast.id.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def failure_reasons(self, broadcast_id: int, limit: int = 5) -> list[tuple[str, int]]:
        rows = await self.session.execute(
            select(BroadcastDelivery.error, func.count(BroadcastDelivery.id))
            .where(
                BroadcastDelivery.broadcast_id == broadcast_id,
                BroadcastDelivery.status == "failed",
            )
            .group_by(BroadcastDelivery.error)
            .order_by(func.count(BroadcastDelivery.id).desc())
            .limit(limit)
        )
        return [(r[0] or "noma'lum", r[1]) for r in rows.all()]
