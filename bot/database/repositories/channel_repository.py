from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from bot.database.models import Channel


class ChannelRepository:
    """Repository for Channel operations"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active_channels(self) -> List[Channel]:
        """Get all active channels ordered by priority"""
        result = await self.session.execute(
            select(Channel)
            .where(Channel.is_active == True)
            .order_by(Channel.priority.desc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, channel_id: int) -> Optional[Channel]:
        """Get channel by ID"""
        result = await self.session.execute(
            select(Channel).where(Channel.channel_id == channel_id)
        )
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> Optional[Channel]:
        """Get channel by username (case-insensitive)."""
        result = await self.session.execute(
            select(Channel).where(Channel.channel_username.ilike(username.strip()))
        )
        return result.scalar_one_or_none()

    async def create(self, channel_id: int, **kwargs) -> Channel:
        """Create new channel"""
        channel = Channel(channel_id=channel_id, **kwargs)
        self.session.add(channel)
        await self.session.commit()
        await self.session.refresh(channel)
        return channel
