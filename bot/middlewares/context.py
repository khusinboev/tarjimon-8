"""Kontekst middleware — har bir update uchun umumiy resurslarni tayyorlaydi.

Eski yondashuvda har bir handler o'zi `AsyncSessionLocal()` ochardi. Bu ikki
muammo tug'diradi: bitta xabar ustida bir nechta tranzaksiya (atomarlik yo'q)
va handler ichida ulanish boshqaruvi (takroriy kod).

Bu middleware bitta update = bitta session = bitta commit qoidasini o'rnatadi.
Handlerlar `data` dan tayyor obyektlarni oladi.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from bot import locales
from bot.database.repositories.user_repository import UserRepository
from bot.database.session import AsyncSessionLocal
from bot.services.events import EventService, SessionTracker

logger = logging.getLogger(__name__)


class ContextMiddleware(BaseMiddleware):
    def __init__(self, redis=None):
        self.redis = redis
        self.tracker = SessionTracker(redis) if redis else None

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")

        async with AsyncSessionLocal() as session:
            data["session"] = session
            data["redis"] = self.redis
            data["events"] = EventService(session)
            # Foydalanuvchi hali yuklanmagan bo'lsa ham handlerlar `t` ga
            # tayanadi — Telegram tilidan boshlang'ich taxmin qo'yamiz.
            data["t"] = locales.get(
                locales.resolve(tg_user.language_code if tg_user else None)
            )

            if tg_user is not None and not tg_user.is_bot:
                repo = UserRepository(session)
                user, is_new = await repo.get_or_create(
                    telegram_id=tg_user.id,
                    username=tg_user.username,
                    first_name=tg_user.first_name,
                    last_name=tg_user.last_name,
                    telegram_lang=tg_user.language_code,
                    is_premium=bool(getattr(tg_user, "is_premium", False)),
                )
                data["user"] = user
                data["is_new_user"] = is_new
                data["session_id"] = (
                    await self.tracker.get(user.id) if self.tracker else None
                )
                # Saqlangan tanlov Telegram tilidan ustun: foydalanuvchi
                # interfeys tilini qo'lda o'zgartirgan bo'lishi mumkin.
                if user.settings is not None:
                    data["t"] = locales.get(user.settings.interface_lang)

            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
