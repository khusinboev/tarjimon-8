from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import Message
from bot.database.session import AsyncSessionLocal
from bot.database.repositories.user_repository import UserRepository
from bot.services.user_service import UserService


class AnalyticsMiddleware(BaseMiddleware):
    """Middleware to track user interactions for analytics"""

    async def __call__(
            self,
            handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
            event: Message,
            data: Dict[str, Any]
    ) -> Any:
        """Track interaction and call handler"""

        if not event.from_user:
            return await handler(event, data)

        async with AsyncSessionLocal() as session:
            user_service = UserService(session)

            # Ensure a user row exists and last_interaction is refreshed.
            await user_service.get_or_create_user(
                telegram_id=event.from_user.id,
                username=event.from_user.username,
                first_name=event.from_user.first_name,
                last_name=event.from_user.last_name,
                language_code=event.from_user.language_code,
            )

        # Call the handler
        return await handler(event, data)
