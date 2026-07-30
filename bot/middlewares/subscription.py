"""Majburiy obuna tekshiruvi.

Har bir handlerda takrorlash o'rniga bitta joyda. Handler chaqirilishidan oldin
ishlaydi va obuna bo'lmagan foydalanuvchini to'xtatadi.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from bot.config.settings import settings
from bot.keyboards.inline import get_subscription_keyboard
from bot.services.events import EventType
from bot.services.subscription_service import SubscriptionService

logger = logging.getLogger(__name__)

# Obunadan qat'i nazar o'tishi kerak bo'lgan update'lar.
ALLOWED_COMMANDS = ("/start", "/help")
ALLOWED_CALLBACKS = ("check_subscription",)


class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("user")
        if user is None:
            return await handler(event, data)

        # Adminlarga majburiy obuna qo'llanmaydi.
        tg_user = data.get("event_from_user")
        if user.role in ("admin", "owner") or (
            tg_user and tg_user.id in settings.ADMIN_USER_IDS
        ):
            return await handler(event, data)

        if isinstance(event, Message):
            # To'lov tasdig'i hech qachon to'silmasligi kerak: pul allaqachon
            # o'tgan, xabarni yutib yuborsak homiylik bazaga yozilmasdi va
            # foydalanuvchi tasdiq ham olmasdi.
            if event.successful_payment is not None:
                return await handler(event, data)
            text = (event.text or "").strip()
            if any(text.startswith(cmd) for cmd in ALLOWED_COMMANDS):
                return await handler(event, data)
        elif isinstance(event, CallbackQuery):
            if event.data in ALLOWED_CALLBACKS:
                return await handler(event, data)
        else:
            return await handler(event, data)

        service = SubscriptionService(data["session"], event.bot)
        try:
            missing = await service.check_user_subscriptions(tg_user.id)
        except Exception:
            # Tekshiruv ishlamasa foydalanuvchini bloklab qo'ymaymiz.
            logger.exception("Obuna tekshiruvi muvaffaqiyatsiz")
            return await handler(event, data)

        if not missing:
            return await handler(event, data)

        events = data.get("events")
        if events:
            await events.log(
                EventType.SUBSCRIPTION_CHECKED,
                user_id=user.id,
                session_id=data.get("session_id"),
                passed=False,
                missing=[channel.channel_username or str(channel.channel_id) for channel in missing],
            )

        t = data["t"]
        markup = get_subscription_keyboard(t, missing)
        if isinstance(event, CallbackQuery):
            await event.answer()
            await event.message.answer(t.SUBSCRIBE_REQUIRED, reply_markup=markup)
        else:
            await event.answer(t.SUBSCRIBE_REQUIRED, reply_markup=markup)
        return None
