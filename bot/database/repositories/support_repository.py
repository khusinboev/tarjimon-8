"""Adminga murojaat yozishmasi."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.database.models import SupportMessage, User


class SupportRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(
        self,
        *,
        user_id: int,
        direction: str,
        text: str,
        admin_chat_id: int,
        admin_message_id: int,
        user_chat_id: int,
        user_message_id: Optional[int] = None,
    ) -> SupportMessage:
        row = SupportMessage(
            user_id=user_id,
            direction=direction,
            text=text,
            admin_chat_id=admin_chat_id,
            admin_message_id=admin_message_id,
            user_chat_id=user_chat_id,
            user_message_id=user_message_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def by_admin_message(
        self, admin_chat_id: int, admin_message_id: int
    ) -> Optional[SupportMessage]:
        """Admin javob berayotgan xabarni topadi."""
        result = await self.session.execute(
            select(SupportMessage)
            # `User.settings` ham yuklanishi SHART: javob foydalanuvchining
            # tilida yuboriladi va `user.settings.interface_lang` o'qiladi.
            # Faqat `user` yuklansa, `settings` ga murojaat async kontekstda
            # `MissingGreenlet` bilan yiqiladi.
            .options(selectinload(SupportMessage.user).selectinload(User.settings))
            .where(
                SupportMessage.admin_chat_id == admin_chat_id,
                SupportMessage.admin_message_id == admin_message_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def by_user_message(
        self, user_chat_id: int, user_message_id: int
    ) -> Optional[SupportMessage]:
        """Foydalanuvchi javob berayotgan xabarni topadi."""
        result = await self.session.execute(
            select(SupportMessage)
            # `User.settings` ham yuklanishi SHART: javob foydalanuvchining
            # tilida yuboriladi va `user.settings.interface_lang` o'qiladi.
            # Faqat `user` yuklansa, `settings` ga murojaat async kontekstda
            # `MissingGreenlet` bilan yiqiladi.
            .options(selectinload(SupportMessage.user).selectinload(User.settings))
            .where(
                SupportMessage.user_chat_id == user_chat_id,
                SupportMessage.user_message_id == user_message_id,
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def thread_size(self, user_id: int) -> int:
        """Shu foydalanuvchi bilan almashilgan xabarlar soni."""
        from sqlalchemy import func

        return (
            await self.session.execute(
                select(func.count(SupportMessage.id)).where(
                    SupportMessage.user_id == user_id
                )
            )
        ).scalar_one()

    async def find_user(self, telegram_id: int) -> Optional[User]:
        """Telegram ID bo'yicha foydalanuvchi.

        `settings` eager yuklanadi: javob foydalanuvchining tilida yuboriladi
        va lazy yuklanish async kontekstda `MissingGreenlet` bilan yiqiladi.
        """
        result = await self.session.execute(
            select(User)
            .options(selectinload(User.settings))
            .where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()
