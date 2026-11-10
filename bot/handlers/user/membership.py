"""Foydalanuvchi botni bloklagani / blokdan chiqargani — darhol (`my_chat_member`).

Muammo (2026-11 auditi): `users.status` ilgari FAQAT tarqatish xatolaridan
yangilanardi. 2026-10 dan tarqatishlar bekor qilingach, 09-28 dan keyin
kelgan 1 409 foydalanuvchining birortasi ham "bloklagan" deb belgilanmagan —
yangi kogortalar churn'i umuman ko'rinmasdi.

Telegram shaxsiy chatda bot holati o'zgarganda o'zi `my_chat_member` update
yuboradi (`kicked` — bloklandi, `member` — qayta ochildi). Bu update turi
`dp.resolve_used_update_types()` orqali avtomatik so'raladi.

Guruhlar bu yerda emas — faqat `chat.type == "private"`.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import ChatMemberUpdated

from bot.database.models import User
from bot.database.repositories.user_repository import UserRepository
from bot.services.events import EventService, EventType

router = Router(name="membership")


@router.my_chat_member(F.chat.type == "private")
async def on_private_membership(
    update: ChatMemberUpdated,
    user: User,
    events: EventService,
    session,
) -> None:
    old, new = update.old_chat_member.status, update.new_chat_member.status
    if old == new:
        return
    repo = UserRepository(session)
    if new == "kicked":
        await repo.mark_blocked(user.id)
        await events.log(EventType.USER_BLOCKED_BOT, user_id=user.id, chat_id=update.chat.id)
    elif new == "member" and old == "kicked":
        # `mark_unblocked` faqat `blocked_bot` holatini qaytaradi — `deleted`
        # yoki `banned` (admin) holatiga tegmaydi.
        await repo.mark_unblocked(user.id)
        await events.log(EventType.USER_UNBLOCKED_BOT, user_id=user.id, chat_id=update.chat.id)
