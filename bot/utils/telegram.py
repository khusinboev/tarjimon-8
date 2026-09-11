"""Telegram'ning zararsiz xatolarini yutadigan yordamchilar.

Callback tugmasi ESKI xabarda bosilganda (48 soatdan o'tgan yoki foydalanuvchi
o'chirgan) Telegram `callback.message`ni `InaccessibleMessage` qilib beradi —
unda `edit_text` yo'q (`AttributeError`), yoki `edit_text` `MESSAGE_ID_INVALID`
bilan yiqiladi. Ilgari bu ~20 soatda 6-7 marta crash bo'lib, foydalanuvchi
HECH QANDAY javob olmasdi (2026-09-11 tahlili).

Qoida: tahrirlab bo'lmasa — yangi xabar yuboriladi; "not modified" — muvaffaqiyat.
"""

from __future__ import annotations

import logging
from typing import Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

logger = logging.getLogger(__name__)

# Telegram'ning zararsiz `Bad Request` matnlari — foydalanuvchiga xato emas.
_BENIGN_MARKERS = (
    "message is not modified",
    "message to edit not found",
    "message_id_invalid",
    "message can't be edited",
    "query is too old",
    "query id is invalid",
)


def is_benign_edit_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _BENIGN_MARKERS)


def _chat_id(callback: CallbackQuery) -> Optional[int]:
    message = callback.message
    chat = getattr(message, "chat", None)
    if chat is not None:
        return chat.id
    return callback.from_user.id if callback.from_user else None


async def safe_edit_text(callback: CallbackQuery, text: str, reply_markup=None) -> bool:
    """Xabarni tahrirlaydi; iloji bo'lmasa yangi xabar yuboradi. `False` — hech
    narsa yetkazib bo'lmadi (masalan foydalanuvchi botni bloklagan)."""
    message = callback.message
    if isinstance(message, Message):
        try:
            await message.edit_text(text, reply_markup=reply_markup)
            return True
        except TelegramBadRequest as exc:
            if not is_benign_edit_error(exc):
                raise
            if "not modified" in str(exc).lower():
                return True
            logger.info("Xabarni tahrirlab bo'lmadi (%s) — yangisi yuboriladi", exc)

    chat_id = _chat_id(callback)
    if chat_id is None:
        return False
    try:
        await callback.bot.send_message(chat_id, text, reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        logger.warning("Yangi xabar ham ketmadi (chat=%s): %s", chat_id, exc)
        return False


async def safe_edit_markup(callback: CallbackQuery, reply_markup) -> bool:
    """Faqat tugmalarni yangilaydi. Tahrirlab bo'lmasa — jim (`False`): tugma
    yangilanmasligi foydalanuvchi uchun xato emas."""
    message = callback.message
    if not isinstance(message, Message):
        return False
    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
        return True
    except TelegramBadRequest as exc:
        if not is_benign_edit_error(exc):
            raise
        return "not modified" in str(exc).lower()
