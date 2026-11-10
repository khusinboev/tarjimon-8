from sqlalchemy import func, select
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
        """Get channel by username (case-insensitive, EXACT match).

        `ilike()` treats `%`/`_` in the input as wildcards — Telegram
        usernames legally contain `_`, so a naive `ilike(username)` could
        match a completely different channel (e.g. "test_channel" also
        matching "testXchannel"). Case-insensitive equality has no such
        ambiguity.
        """
        result = await self.session.execute(
            select(Channel).where(func.lower(Channel.channel_username) == username.strip().lower())
        )
        return result.scalar_one_or_none()

    async def create(self, channel_id: int, **kwargs) -> Channel:
        """Create new channel.

        Chaqiruvchi COMMIT qiladi (bu yerda EMAS) — aks holda bu yerdagi
        ichki commit `AdminService.log_action()` bilan bitta tranzaksiyaga
        sig'may, kanal qo'shish audit jurnaliga (`admin_actions`) HECH
        QACHON tushmasdi (boshqa hamma admin amali tushadi). `flush()` esa
        UNIQUE cheklov kabi xatolarni DARHOL, chaqiruvchining mavjud
        `except IntegrityError` bloki hali ishlay oladigan paytda ko'rsatadi.
        """
        channel = Channel(channel_id=channel_id, **kwargs)
        self.session.add(channel)
        await self.session.flush()
        return channel
