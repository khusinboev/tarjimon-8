from datetime import datetime
from sqlalchemy import update, select
from sqlalchemy.ext.asyncio import AsyncSession
from bot.database.models import Broadcast, BroadcastDelivery


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
            started_at=datetime.utcnow(),
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
                finished_at=datetime.utcnow(),
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
                finished_at=datetime.utcnow(),
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
                finished_at=datetime.utcnow(),
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
