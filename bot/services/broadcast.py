"""Xabar tarqatish tezligini boshqarish.

Muammo: ketma-ket yuborishda tezlik tarmoq kutishi bilan cheklanadi. Telegram'ga
bitta so'rovning borib-kelishi ~200ms, ya'ni `await` ni birin-ketin chaqirsak
soniyada ~5 xabardan oshmaydi — Telegram limiti (~30/sek) esa hali uzoq.
37 445 xabar shu tezlikda 2.2 soat oldi.

Yechim ikki qismdan iborat va ularni aralashtirmaslik muhim:

  **Semafor** — bir vaqtda ochiq so'rovlar soni. Tarmoq kutishini yashiradi,
  lekin tezlikni belgilamaydi.

  **RateLimiter** — soniyada Telegram'ga necha chaqiruv ketishi. Haqiqiy
  tezlikni aynan shu belgilaydi.

Faqat semafor qo'yilsa tezlik tarmoq holatiga qarab o'zgarib turadi va limitga
tegib `RetryAfter` yeyish mumkin. Faqat rate-limiter qo'yilsa esa ketma-ket
kutish saqlanib qoladi. Ikkisi birga: tekis va oldindan aytib bo'ladigan oqim.
"""

from __future__ import annotations

import asyncio


class RateLimiter:
    """Token-bucket: soniyasiga cheklangan sondagina chaqiruv o'tishini kafolatlaydi.

    Har bir `wait()` navbatdagi "slot" ni band qiladi va shu vaqt kelguncha
    kutadi. Qulf ostida faqat hisob-kitob bo'ladi, kutish esa qulfdan tashqarida
    — aks holda parallel yuboruvchilar bir-birini to'sib qo'yardi.
    """

    def __init__(self, rate_per_sec: float):
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec musbat bo'lishi kerak")
        self._interval = 1.0 / rate_per_sec
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            start = max(self._next_slot, now)
            self._next_slot = start + self._interval
            delay = start - now
        if delay > 0:
            await asyncio.sleep(delay)

    def pause(self, seconds: float) -> None:
        """Barcha yuboruvchilarni birgalikda kechiktiradi.

        `RetryAfter` kelganda chaqiriladi: limitga bitta so'rov tegsa, qolgani
        ham tegadi. Bittasi kutib qolganda boshqalari yuborishda davom etsa,
        flood-wait cho'zilib ketardi.
        """
        loop = asyncio.get_running_loop()
        self._next_slot = max(self._next_slot, loop.time() + seconds)


# Telegram global limiti ~30/sek. 15 ni olamiz: qolgan yarmi jonli
# foydalanuvchilarning tarjima so'rovlariga qoladi — tarqatish paytida bot
# javob bermay qolmasligi kerak.
DEFAULT_RATE_PER_SEC = 15.0

# Bir vaqtda ochiq so'rovlar. 10 ta tarmoq kutishini yashirish uchun yetarli;
# ko'paytirish tezlikni oshirmaydi, chunki chegara RateLimiter'da.
DEFAULT_CONCURRENCY = 10


# ─────────────────────────────────────────────────────────────
#  Xatolarni tasniflash
# ─────────────────────────────────────────────────────────────
# Bu xatolar chatning umuman mavjud emasligini bildiradi. Qayta urinish ham,
# keyingi tarqatishlarda qayta yuborish ham foydasiz — chat qaytmaydi.
#
# Ma'nolari:
#   chat not found            — akkaunt o'chirilgan yoki shaxsiy chat ochilmagan
#   PEER_ID_INVALID           — id umuman yaroqsiz (eski migratsiyadan qolgan)
#   USER_BOT_TO_BOT_DISABLED  — nishon o'zi bot, botlar bir-biriga yoza olmaydi
#   user is deactivated       — Telegram akkauntni o'chirgan
#
# `forbidden` va "bot was blocked by the user" bu ro'yxatda ataylab YO'Q:
# ular "bloklagan" degani, foydalanuvchi botni qayta ochsa holat tiklanadi.
# Ularni `deleted` qilish qaytib keladigan foydalanuvchini yo'qotish bo'lardi.
PERMANENT_ERROR_MARKERS = (
    "chat not found",
    "user_bot_to_bot_disabled",
    "peer_id_invalid",
    "user is deactivated",
    "user_deactivated",
    "chat_id is empty",
    "bot can't initiate conversation",
)


def is_permanently_unreachable(error_text: str | None) -> bool:
    """Xato "bu chatga hech qachon yetib bo'lmaydi" degani bo'lsa — True.

    Shunday foydalanuvchilar `deleted` holatiga o'tkaziladi va keyingi
    tarqatishlar ro'yxatiga umuman tushmaydi. Aks holda ular har safar
    qayta urinilib, vaqt sarflab, xato statistikasini shishirardi.
    """
    if not error_text:
        return False
    lowered = error_text.lower()
    return any(marker in lowered for marker in PERMANENT_ERROR_MARKERS)
