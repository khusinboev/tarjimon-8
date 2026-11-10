"""Ovoz (TTS) xatolarining DARAJASI bo'yicha ogohlantirish.

2026-10-12 saboq: `edge-tts` Microsoft tomonida protokol o'zgargach 100%
so'rovlarda `403 Invalid response status` bilan yiqildi, lekin bir oy
davomida birorta ogohlantirish kelmadi — TTS xatolari faqat `events`
jadvaliga yozilardi, hech kim ularni kuzatmasdi.

`error_rate` (tarjima) bilan bir xil yondashuv va yordamchilar: 5 daqiqalik
chelaklar, oxirgi 15 daqiqa oynasi. TTS so'rovlari tarjimadan ancha kam
(oyiga ~425 foydalanuvchi), shuning uchun `MIN_TOTAL` past, chegara esa
baland — vaqtinchalik tarmoq uzilishlari (bitta-ikkita timeout) emas,
provayderning to'liq ishdan chiqishi ushlanadi.

Faqat PROVAYDER natijasi hisoblanadi: kesh (`file_id`) orqali yuborilgan
ovoz, "matn juda uzun", "ovozli xabar taqiqlangan" kabi holatlar provayder
salomatligi haqida hech narsa demaydi — ular hisobga kirmaydi.

Redis yo'q bo'lsa — jim (bu himoya TTS'ning o'ziga hech qachon xalaqit
bermasligi kerak). `prefix` — smoke-test uchun.
"""

from __future__ import annotations

import logging
from typing import Optional

from aiogram import Bot

from bot.services import error_rate
from bot.services.admin_alerts import alert_admins_once
from bot.utils.text import html_escape, truncate

logger = logging.getLogger(__name__)

MIN_TOTAL = 5
THRESHOLD = 0.50
KEY_PREFIX = "tts:stat"
ALERT_KEY = "tts_error_rate"


async def record(redis, ok: bool, *, prefix: str = KEY_PREFIX) -> None:
    await error_rate.record(redis, ok, prefix=prefix)


async def window_stats(redis, *, prefix: str = KEY_PREFIX) -> tuple[int, int]:
    return await error_rate.window_stats(redis, prefix=prefix)


def should_alert(ok: int, err: int) -> bool:
    """Sof mantiq: kamida `MIN_TOTAL` so'rov va xato ulushi `THRESHOLD`dan
    QAT'IY katta (aynan 50% — hali "yarim ishlayapti")."""
    total = ok + err
    return total >= MIN_TOTAL and err / total > THRESHOLD


def alert_text(ok: int, err: int, last_error: Optional[str] = None) -> str:
    total = ok + err
    minutes = error_rate.BUCKET_SECONDS * error_rate.WINDOW_BUCKETS // 60
    text = (
        f"🔇 <b>Ovoz (TTS) ishlamayapti</b> — oxirgi {minutes} daqiqada "
        f"<b>{err}/{total}</b> so'rov yiqildi ({err / total:.0%}).\n"
    )
    if last_error:
        # Provayder xabari ichida `<`/`&` bo'lishi mumkin — HTML'ni buzmasin.
        text += f"Oxirgi xato: <code>{html_escape(truncate(last_error, 300))}</code>\n"
    text += (
        "Odatda <code>edge-tts</code> eskirgan bo'ladi: "
        "<code>pip install -U edge-tts</code> va botni qayta ishga tushiring."
    )
    return text


async def check_and_alert(
    bot: Optional[Bot],
    redis,
    *,
    last_error: Optional[str] = None,
    prefix: str = KEY_PREFIX,
) -> bool:
    """Xato ulushi chegaradan oshgan bo'lsa adminga BIR MARTA xabar va `True`."""
    ok, err = await window_stats(redis, prefix=prefix)
    if not should_alert(ok, err):
        return False
    logger.warning("TTS xato darajasi yuqori: %s/%s", err, ok + err)
    await alert_admins_once(bot, redis, ALERT_KEY, alert_text(ok, err, last_error))
    return True
