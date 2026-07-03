from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from bot.database.models import User
from bot.database.repositories.user_repository import UserRepository


class UserService:
    """Service for user operations"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = UserRepository(session)

    async def get_or_create_user(
        self,
        telegram_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        language_code: Optional[str] = None,
    ) -> User:
        """Get existing user or create new one"""
        user = await self.repo.get_by_telegram_id(telegram_id)

        if not user:
            user = await self.repo.create(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                language_code=language_code,
            )
        else:
            await self.repo.update_last_interaction(telegram_id)

        return user
