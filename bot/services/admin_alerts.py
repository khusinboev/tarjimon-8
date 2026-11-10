"""Adminga operatsion muammolar (limit/kvota tugashi, provayder ishlamay
qolishi) haqida Telegram orqali xabar berish.

Bir xil muammo har so'rovda emas — `COOLDOWN_SECONDS` oralig'ida FAQAT BIR
MARTA yuboriladi (Redis'dagi `NX` — faqat mavjud bo'lmasa yoz — bayrog'i
bilan). Aks holda minglab tarjima so'rovi bitta tugagan kvota haqida
minglab xabar yuborardi.

Redis mavjud bo'lmasa (masalan test muhitida) — dedup o'tkazib yuboriladi,
xabar baribir jo'natiladi: bu yerda "ko'rinmaslik" xavfi "spam" xavfidan
yomonroq.
"""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot

from bot.config.settings import settings

logger = logging.getLogger(__name__)

COOLDOWN_SECONDS = 6 * 60 * 60  # 6 soat — muammo hali tuzalmagan bo'lsa qayta eslatadi.


async def alert_admins_once(
    bot: Optional[Bot], redis, dedup_key: str, text: str
) -> None:
    """`bot` yo'q bo'lsa (masalan bot bermagan chaqiruvchi) — jim o'tkaziladi."""
    if bot is None:
        return

    if redis is not None:
        try:
            first_time = await redis.set(f"admin_alert:{dedup_key}", "1", ex=COOLDOWN_SECONDS, nx=True)
        except Exception:
            first_time = True  # Redis nosoz — dedup imkonsiz, baribir yuboramiz.
        if not first_time:
            return

    for admin_id in settings.ADMIN_USER_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logger.warning("Limit ogohlantirishini adminga (%s) yuborib bo'lmadi", admin_id)
