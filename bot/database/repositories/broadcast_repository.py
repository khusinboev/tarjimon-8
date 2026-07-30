from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from bot.database.models import Broadcast, BroadcastDelivery
from bot.services.events import utcnow


class BroadcastRepository:
    """Repository for broadcast operations"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_broadcast(self, created_by: int, mode: str, content_preview: str | None, total_targets: int) -> Broadcast:
        broadcast = Broadcast(
            created_by=created_by,
            mode=mode,
            content_preview=content_preview,
            total_targets=total_targets,
            status="running",
            started_at=utcnow(),
        )
        self.session.add(broadcast)
        await self.session.commit()
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

    async def finish_broadcast(self, broadcast_id: int, success_count: int, failed_count: int) -> None:
        await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                status="completed",
                success_count=success_count,
                failed_count=failed_count,
                finished_at=utcnow(),
            )
        )
        await self.session.commit()

    async def fail_broadcast(self, broadcast_id: int, failed_count: int) -> None:
        await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                status="failed",
                failed_count=failed_count,
                finished_at=utcnow(),
            )
        )
        await self.session.commit()

    async def get_status(self, broadcast_id: int) -> str | None:
        result = await self.session.execute(
            select(Broadcast.status).where(Broadcast.id == broadcast_id)
        )
        return result.scalar_one_or_none()

    async def request_cancel(self, broadcast_id: int) -> bool:
        result = await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id, Broadcast.status == "running")
            .values(status="cancel_requested")
        )
        await self.session.commit()
        return (result.rowcount or 0) > 0

    async def mark_cancelled(self, broadcast_id: int, success_count: int, failed_count: int) -> None:
        await self.session.execute(
            update(Broadcast)
            .where(Broadcast.id == broadcast_id)
            .values(
                status="cancelled",
                success_count=success_count,
                failed_count=failed_count,
                finished_at=utcnow(),
            )
        )
        await self.session.commit()

    async def get_running_broadcasts(self) -> list[Broadcast]:
        result = await self.session.execute(
            select(Broadcast)
            .where(Broadcast.status.in_(["running", "cancel_requested"]))
            .order_by(Broadcast.created_at.desc())
        )
        return list(result.scalars().all())

    async def stats(self, limit: int = 5) -> list[dict]:
        """Oxirgi tarqatishlar bo'yicha yetkazish hisobi.

        `broadcasts.success_count` faqat tarqatish tugagach yoziladi, shuning
        uchun ishlab turgan tarqatish uchun `broadcast_deliveries` dan
        hisoblaymiz — admin jarayon davomida ham holatni ko'rishi kerak.
        """
        rows = await self.session.execute(
            select(
                Broadcast.id,
                Broadcast.status,
                Broadcast.total_targets,
                Broadcast.content_preview,
                Broadcast.started_at,
                Broadcast.finished_at,
                func.count(BroadcastDelivery.id)
                .filter(BroadcastDelivery.status == "delivered")
                .label("delivered"),
                func.count(BroadcastDelivery.id)
                .filter(BroadcastDelivery.status == "failed")
                .label("failed"),
            )
            .outerjoin(BroadcastDelivery, BroadcastDelivery.broadcast_id == Broadcast.id)
            .group_by(
                Broadcast.id,
                Broadcast.status,
                Broadcast.total_targets,
                Broadcast.content_preview,
                Broadcast.started_at,
                Broadcast.finished_at,
            )
            .order_by(Broadcast.id.desc())
            .limit(limit)
        )
        return [
            {
                "id": r.id,
                "status": r.status,
                "total": r.total_targets,
                "preview": r.content_preview,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "delivered": r.delivered,
                "failed": r.failed,
            }
            for r in rows.all()
        ]

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
