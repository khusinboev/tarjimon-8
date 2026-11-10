from __future__ import annotations

from types import ModuleType

from aiogram import F, Router
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import User
from bot.keyboards.inline import get_subscription_keyboard
from bot.utils.telegram import safe_edit_markup, safe_edit_text
from bot.keyboards.user import main_menu
from bot.services.events import EventService, EventType
from bot.services.subscription_service import SubscriptionService

router = Router(name="subscription")


@router.callback_query(F.data == "check_subscription")
async def check_subscription(
    callback: CallbackQuery,
    session: AsyncSession,
    user: User,
    events: EventService,
    session_id,
    t: ModuleType,
) -> None:
    service = SubscriptionService(session, callback.bot)
    missing = await service.check_user_subscriptions(callback.from_user.id)

    await events.log(
        EventType.SUBSCRIPTION_CHECKED,
        user_id=user.id,
        chat_id=callback.message.chat.id if callback.message else None,
        session_id=session_id,
        passed=not missing,
        missing=[c.channel_username or str(c.channel_id) for c in missing],
    )

    if not missing:
        await events.log(
            EventType.SUBSCRIPTION_JOINED,
            user_id=user.id,
            session_id=session_id,
        )
        await callback.answer("✅", show_alert=False)
        await safe_edit_text(callback, t.SUBSCRIBE_OK)
        await callback.bot.send_message(
            callback.from_user.id, t.SEND_TEXT_PROMPT, reply_markup=main_menu(t)
        )
        return

    await callback.answer(t.SUBSCRIBE_STILL_MISSING, show_alert=True)
    await safe_edit_markup(callback, get_subscription_keyboard(t, missing))
