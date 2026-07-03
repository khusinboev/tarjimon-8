import asyncio
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot
from typing import List
from aiogram.exceptions import TelegramRetryAfter, TelegramAPIError
from bot.database.models import Channel
from bot.database.repositories.channel_repository import ChannelRepository
from bot.database.redis import get_redis

_CACHE_TTL = 300  # 5 daqiqa
logger = logging.getLogger(__name__)


class SubscriptionService:
    """Service for subscription operations"""

    def __init__(self, session: AsyncSession, bot: Bot):
        self.session = session
        self.bot = bot
        self.repo = ChannelRepository(session)

    async def check_user_subscriptions(self, user_id: int) -> List[Channel]:
        """
        Check if user is subscribed to all required channels.
        Returns list of channels user is NOT subscribed to.
        Redis cache: if user was fully subscribed recently, skip API calls.
        """
        cache_key = f"sub:{user_id}"
        try:
            cached = await get_redis().get(cache_key)
            if isinstance(cached, bytes):
                cached = cached.decode("utf-8", errors="ignore")
            if cached == "ok":
                return []
        except Exception:
            pass

        channels = await self.repo.get_active_channels()
        not_subscribed = []

        for channel in channels:
            is_subscribed = False
            for attempt in range(2):
                try:
                    member = await asyncio.wait_for(
                        self.bot.get_chat_member(chat_id=channel.channel_id, user_id=user_id),
                        timeout=8,
                    )
                    member_status = getattr(member.status, "value", str(member.status))
                    is_subscribed = member_status not in {"left", "kicked"}
                    break
                except asyncio.TimeoutError:
                    if attempt == 1:
                        logger.warning("Timeout while checking subscription user=%s channel=%s", user_id, channel.channel_id)
                except TelegramRetryAfter as exc:
                    wait_time = max(int(getattr(exc, "retry_after", 1)), 1)
                    await asyncio.sleep(wait_time)
                except TelegramAPIError as exc:
                    logger.info(
                        "API error while checking subscription user=%s channel=%s: %s",
                        user_id,
                        channel.channel_id,
                        exc,
                    )
                    break
                except Exception:
                    logger.exception(
                        "Unexpected error while checking subscription user=%s channel=%s",
                        user_id,
                        channel.channel_id,
                    )
                    break

            if not is_subscribed:
                not_subscribed.append(channel)

        if not not_subscribed:
            try:
                await get_redis().setex(cache_key, _CACHE_TTL, "ok")
            except Exception:
                pass

        return not_subscribed
