"""Tarjima xatolarining DARAJASI bo'yicha ogohlantirish.

2026-09-04…10 saboq: bir hafta davomida tarjimalarning 11–48% i yiqildi,
lekin birorta ogohlantirish kelmadi — mavjud alert faqat "uchala daraja ham
yiqildi" holatini ushlardi, `deep_translator` esa "ishlagan" (sifatsiz/
xato bo'lsa ham) hisoblanardi.

Bu modul provayder emas, NATIJANI kuzatadi: oxirgi `WINDOW_BUCKETS ×
BUCKET_SECONDS` (15 daqiqa) ichida kamida `MIN_TOTAL` so'rov bo'lib, xato
ulushi `THRESHOLD`dan oshsa — adminga bir marta (`alert_admins_once`
cooldown'i bilan) xabar. Redis yo'q bo'lsa — jim (bu himoya tarjimaning
o'ziga hech qachon xalaqit bermasligi kerak).

`prefix` — smoke-test uchun: haqiqiy statistikani ifloslamasdan sinash.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from aiogram import Bot

from bot.database.redis import atomic_rate_incr
from bot.services.admin_alerts import alert_admins_once

logger = logging.getLogger(__name__)

BUCKET_SECONDS = 300
WINDOW_BUCKETS = 3
MIN_TOTAL = 30
THRESHOLD = 0.10
KEY_PREFIX = "tr:stat"


def _bucket(now: Optional[float] = None) -> int:
    return int((now if now is not None else time.time()) // BUCKET_SECONDS)


async def record(redis, ok: bool, *, prefix: str = KEY_PREFIX) -> None:
    if redis is None:
        return
    key = f"{prefix}:{'ok' if ok else 'err'}:{_bucket()}"
    try:
        # TTL oynadan uzunroq — eski chelaklar o'zi o'chib ketadi.
        await atomic_rate_incr(redis, key, BUCKET_SECONDS * (WINDOW_BUCKETS + 1))
    except Exception:
        pass


async def window_stats(redis, *, prefix: str = KEY_PREFIX) -> tuple[int, int]:
    """(muvaffaqiyat, xato) — oxirgi oyna bo'yicha. Redis yo'q/nosoz — (0, 0)."""
    if redis is None:
        return 0, 0
    current = _bucket()
    ok_keys = [f"{prefix}:ok:{current - i}" for i in range(WINDOW_BUCKETS)]
    err_keys = [f"{prefix}:err:{current - i}" for i in range(WINDOW_BUCKETS)]
    try:
        values = await redis.mget(*ok_keys, *err_keys)
    except Exception:
        return 0, 0
    nums = [int(v) if v else 0 for v in values]
    return sum(nums[:WINDOW_BUCKETS]), sum(nums[WINDOW_BUCKETS:])


async def check_and_alert(bot: Optional[Bot], redis, *, prefix: str = KEY_PREFIX) -> bool:
    """Xato ulushi chegaradan oshgan bo'lsa adminga xabar beradi va `True`."""
    ok, err = await window_stats(redis, prefix=prefix)
    total = ok + err
    if total < MIN_TOTAL:
        return False
    ratio = err / total
    if ratio < THRESHOLD:
        return False
    minutes = BUCKET_SECONDS * WINDOW_BUCKETS // 60
    logger.warning("Tarjima xato darajasi yuqori: %s/%s (%.0f%%)", err, total, ratio * 100)
    await alert_admins_once(
        bot, redis, "translate_error_rate",
        f"🚨 <b>Tarjima xatolari ko'paydi</b> — oxirgi {minutes} daqiqada "
        f"<b>{err}/{total}</b> so'rov yiqildi ({ratio:.0%}).\n"
        "Provayder loglarini tekshiring: <code>journalctl -u tarjimon8 | grep ishlamadi</code>",
    )
    return True
