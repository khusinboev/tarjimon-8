"""Global xato ushlagich — handler'da ushlanmagan istisno bo'lsa
foydalanuvchi JIM qolmasin.

2026-09-11 tahlili: ~20 soatda 10 ta ushlanmagan istisno, har birida
foydalanuvchi hech qanday javob olmagan (bot "jim qolgan"). Bu handler
oxirgi to'siq: log + foydalanuvchiga qisqa xabar + adminga (dedup bilan)
signal. Aniq holatlarni (ovozli xabar taqiqi, rasm yuklab olinmasligi,
eski xabarni tahrirlash) o'z joyida ushlash afzal — bu yerga faqat
KUTILMAGAN narsa tushishi kerak.

`data` — middleware'lar to'ldirgan kontekst (`t`, `redis`, `bot`, …).
DIQQAT: `data["session"]` bu paytda allaqachon yopilgan — ishlatilmaydi.
"""

from __future__ import annotations

import logging
import traceback

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ErrorEvent

from bot import locales
from bot.services.admin_alerts import alert_admins_once
from bot.utils.telegram import is_benign_edit_error
from bot.utils.text import html_escape

logger = logging.getLogger(__name__)


def _last_project_frame(exc: BaseException) -> str:
    """Traceback'dagi oxirgi `bot/` fayli — adminga qisqa manzil."""
    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    for frame in reversed(frames):
        if "/bot/" in frame.filename:
            short = frame.filename.split("/bot/", 1)[1]
            return f"bot/{short}:{frame.lineno} {frame.name}"
    return "—"


async def on_error(event: ErrorEvent, **data) -> bool:
    exc = event.exception
    update = event.update

    # Foydalanuvchi botni bloklagan — javob berib bo'lmaydi, xato ham emas.
    if isinstance(exc, TelegramForbiddenError):
        return True
    # Eski/o'chirilgan xabarni tahrirlash — zararsiz.
    if isinstance(exc, TelegramBadRequest) and is_benign_edit_error(exc):
        return True

    logger.error(
        "Ushlanmagan istisno (update=%s): %s: %s",
        getattr(update, "update_id", "?"), type(exc).__name__, exc,
        exc_info=exc,
    )

    bot = data.get("bot")
    t = data.get("t")
    if t is None:
        tg_user = data.get("event_from_user")
        t = locales.get(locales.resolve(getattr(tg_user, "language_code", None)))

    chat_id = None
    if update.message is not None:
        chat_id = update.message.chat.id
    elif update.callback_query is not None:
        callback = update.callback_query
        chat = getattr(callback.message, "chat", None)
        chat_id = chat.id if chat is not None else callback.from_user.id
        try:
            await callback.answer()
        except Exception:
            pass

    if chat_id is not None and bot is not None:
        try:
            await bot.send_message(chat_id, t.ERROR_DEFAULT)
        except Exception:
            logger.warning("Xato xabarini yuborib bo'lmadi (chat=%s)", chat_id)

    location = _last_project_frame(exc)
    await alert_admins_once(
        bot, data.get("redis"), f"unhandled:{type(exc).__name__}:{location}",
        "🐞 <b>Ushlanmagan xato</b>\n"
        f"<code>{html_escape(type(exc).__name__)}: {html_escape(str(exc)[:300])}</code>\n"
        f"📍 <code>{html_escape(location)}</code>",
    )
    return True
