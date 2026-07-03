from datetime import datetime, timedelta
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from bot.database.models import User


class UserRepository:

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        result = await self.session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def create(self, telegram_id: int, **kwargs) -> User:
        user = User(telegram_id=telegram_id, **kwargs)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def update_last_interaction(self, telegram_id: int) -> None:
        await self.session.execute(
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(last_interaction=datetime.utcnow())
        )
        await self.session.commit()

    async def get_total_users(self) -> int:
        result = await self.session.execute(select(func.count(User.id)))
        return result.scalar_one()

    async def get_active_users(self, days: int = 7) -> int:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(func.count(User.id))
            .where(User.last_interaction >= cutoff_date)
        )
        return result.scalar_one()

    async def get_all_active_user_ids(self, exclude_user_id: Optional[int] = None) -> List[int]:
        query = select(User.telegram_id).where(User.is_active == True)
        if exclude_user_id is not None:
            query = query.where(User.telegram_id != exclude_user_id)
        result = await self.session.execute(query)
        return [row[0] for row in result.all()]

    async def mark_user_blocked(self, telegram_id: int) -> None:
        await self.session.execute(
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(
                is_blocked=True,
                is_active=False,
                blocked_at=datetime.utcnow(),
            )
        )
        await self.session.commit()
