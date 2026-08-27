"""Adminga murojaat yozishmasi."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.database.models import AdminReplyTarget, SupportMessage, User
from bot.services.events import utcnow


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

    async def find_user_by_id(self, user_id: int) -> Optional[User]:
        """Ichki `users.id` bo'yicha — "↩️ Javob yozish" tugmasi shu ID'ni
        callback_data'ga qo'yadi (telegram_id emas, chunki u ba'zan
        Telegram tomonidan uzoq bo'lishi mumkin)."""
        result = await self.session.execute(
            select(User).options(selectinload(User.settings)).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    async def set_pinned_target(self, admin_chat_id: int, target_user_id: int) -> None:
        """Admin "↩️ Javob yozish" tugmasini bosganda chaqiriladi.

        Muddatsiz saqlanadi (FSM emas) — `get_pinned_target` uni
        ISHLATGANDAN keyin o'chiradi, vaqt bo'yicha emas.
        """
        stmt = pg_insert(AdminReplyTarget).values(
            admin_chat_id=admin_chat_id, target_user_id=target_user_id
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[AdminReplyTarget.admin_chat_id],
            set_={"target_user_id": target_user_id, "set_at": utcnow()},
        )
        await self.session.execute(stmt)

    async def get_pinned_target(self, admin_chat_id: int) -> Optional[User]:
        """Tanlangan suhbatni qaytaradi va DARHOL o'chiradi (bir martalik —
        keyingi oddiy xabar shu foydalanuvchiga ketadi, undan keyingilari
        ESA ENDI oddiy tarjima sifatida ishlanadi, tasodifan boshqa
        foydalanuvchiga ketib qolmasin deb)."""
        result = await self.session.execute(
            select(AdminReplyTarget)
            .options(selectinload(AdminReplyTarget.target_user).selectinload(User.settings))
            .where(AdminReplyTarget.admin_chat_id == admin_chat_id)
        )
        pin = result.scalar_one_or_none()
        if pin is None:
            return None
        target = pin.target_user
        await self.session.delete(pin)
        return target
