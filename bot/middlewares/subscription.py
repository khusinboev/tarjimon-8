"""Majburiy obuna tekshiruvi.

Har bir handlerda takrorlash o'rniga bitta joyda. Handler chaqirilishidan oldin
ishlaydi va obuna bo'lmagan foydalanuvchini to'xtatadi.
"""

from __future__ import annotations

import logging
import re
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
_HTML_TAG = re.compile(r"<[^>]+>")


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

        tg_user = data.get("event_from_user")
        is_admin = user.role in ("admin", "owner") or (
            tg_user and tg_user.id in settings.ADMIN_USER_IDS
        )

        # Admin tomonidan taqiqlangan — tarjima va boshqa oqimlar yopiq.
        # Env/admin rollari o'tadi (o'zini yoki boshqa adminni taqiqlash xatosidan
        # qutulish va panel ishlashi uchun).
        if user.status == "banned" and not is_admin:
            t = data.get("t")
            ban_text = (
                t.BANNED.format(admin=settings.ADMIN_USERNAME)
                if t is not None
                else "🚫 Hisobingiz bloklangan."
            )
            if isinstance(event, Message):
                await event.answer(ban_text)
            elif isinstance(event, CallbackQuery):
                # Alert HTML ni render qilmaydi; uzunlik 200 belgidan oshmasin.
                plain = _HTML_TAG.sub("", ban_text)
                await event.answer(plain[:200], show_alert=True)
            return

        # Adminlarga majburiy obuna qo'llanmaydi.
        if is_admin:
            return await handler(event, data)

        if isinstance(event, Message):
            # To'lov tasdig'i hech qachon to'silmasligi kerak: pul allaqachon
            # o'tgan, xabarni yutib yuborsak homiylik bazaga yozilmasdi va
            # foydalanuvchi tasdiq ham olmasdi.
            if event.successful_payment is not None:
                return await handler(event, data)
            text = (event.text or "").strip()
            # `startswith` emas — aniq buyruq tokeni: aks holda `/starting`
            # yoki `/startXYZ` kabi ro'yxatda YO'Q buyruqlar ham
            # `/start` bilan boshlangani uchun majburiy obunani chetlab
            # o'tardi. `@BotUsername` qo'shimchasi va argumentlar
            # (`/start <referral_id>`) hisobga olinadi.
            command = text.split(maxsplit=1)[0].split("@")[0] if text else ""
            if command in ALLOWED_COMMANDS:
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
