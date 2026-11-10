"""Tarqatishdagi Telegram xatolarini tasniflash.

manager-bot (github.com/khusinboev/manager-bot, `worker/errors.py`) loyihasidan
o'rganib moslashtirildi — u yerda bu "tizimning eng nozik qismi" deb
belgilangan, sababi asosli: noto'g'ri tasnif oqibati og'ir.

Har bir muvaffaqiyatsiz yuborish uch toifadan biriga tushadi:

**PASSIV** — foydalanuvchiga bog'liq, DOIMIY sabab (bloklagan, hisob
o'chirilgan, chat topilmadi). Bunday qabul qiluvchi shu tarqatishda ham,
keyingilarida ham o'tkazib yuboriladi.

**VAQTINCHALIK** — tarmoq, Telegram tomonidagi nosozlik yoki tezlik
chegarasi. Qabul qiluvchining aybi EMAS — passiv qilinmaydi, qayta uriladi.

**FATAL** — xabarning O'ZIGA yoki manba xabarga tegishli (bo'sh matn,
buzuq HTML, manba xabar topilmadi). Bular HAR BIR qabul qiluvchida bir xil
takrorlanadi, shuning uchun qayta urinish yoki keyingi userga o'tish
foyda bermaydi — butun tarqatish darhol to'xtatiladi.

⚠️ Bu ajratish noto'g'ri bo'lsa oqibati og'ir: bitta tarmoq uzilishida
minglab tirik foydalanuvchi bekorga passiv bo'lib qolishi yoki xabarning
o'zi buzuq bo'lsa-yu shu bilinmasdan hammaga soatlab behuda urinilishi
mumkin. Shuning uchun qoida qat'iy — **shubha bo'lsa passiv/fatal
QILINMAYDI**, oddiy "vaqtinchalik xato" deb hisoblanadi.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)


class Kind:
    # Doimiy — passiv qilinadi (`user_repository` dagi mavjud holatlarga tushiriladi)
    BLOCKED = "blocked"
    DEACTIVATED = "deactivated"
    CHAT_NOT_FOUND = "chat_not_found"

    # Vaqtinchalik — qayta uriladi, foydalanuvchi yo'qotilmaydi
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    SERVER = "server"

    # Sozlama/xabar xatosi — butun tarqatish to'xtatiladi
    BAD_TOKEN = "bad_token"
    BAD_SOURCE = "bad_source"
    BAD_MESSAGE = "bad_message"

    # Noma'lum — passiv qilinmaydi, faqat yoziladi
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Classification:
    kind: str
    detail: str
    passivate: bool
    retriable: bool
    fatal: bool = False
    retry_after: int = 0


# Manba xabar bilan bog'liq — HECH KIMGA yetkazib bo'lmaydi (masalan admin
# manba xabarni o'chirib yuborgan). Passiv tekshiruvidan OLDIN tekshiriladi:
# bu qabul qiluvchining aybi emas.
_FATAL_SOURCE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"message to copy not found", re.I),
    re.compile(r"message to forward not found", re.I),
    re.compile(r"message_id_invalid", re.I),
    re.compile(r"message can'?t be copied", re.I),
    re.compile(r"message to (be )?forward(ed)? not found", re.I),
)

# Xabarning O'ZIGA bog'liq — hamma qabul qiluvchida bir xil takrorlanadi.
_FATAL_MESSAGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"message text is empty", re.I),
    re.compile(r"message is too long", re.I),
    re.compile(r"message_too_long", re.I),
    re.compile(r"caption is too long", re.I),
    re.compile(r"media_caption_too_long", re.I),
    re.compile(r"can'?t parse entities", re.I),
    re.compile(r"can'?t find end of the entity", re.I),
    re.compile(r"unsupported (start )?tag", re.I),
    re.compile(r"entity_bounds_invalid", re.I),
    re.compile(r"wrong file identifier", re.I),
    re.compile(r"wrong remote file id", re.I),
    re.compile(r"there is no (text|caption) in the message to (edit|copy)", re.I),
)

_PASSIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"bot was blocked by the user", re.I), Kind.BLOCKED),
    (re.compile(r"user is deactivated", re.I), Kind.DEACTIVATED),
    (re.compile(r"chat not found", re.I), Kind.CHAT_NOT_FOUND),
    (re.compile(r"user not found", re.I), Kind.CHAT_NOT_FOUND),
    (re.compile(r"peer_id_invalid", re.I), Kind.CHAT_NOT_FOUND),
    # DIQQAT: shu uchtasi ilgari faqat `broadcast.py`dagi (jonli quvurda
    # ISHLATILMAYDIGAN, faqat `scripts/mark_unreachable.py` va
    # `broadcast_relaunch.py` kabi alohida skriptlarda ishlatiladigan)
    # `PERMANENT_ERROR_MARKERS`da bor edi — jonli tarqatish esa bunday
    # nishonlarni "noma'lum xato" deb har safar qayta-qayta urinib
    # turardi. Ikkala ro'yxat qo'lda sinxron saqlanishi kerak — yangi
    # "doimiy o'chirilgan" belgisi topilsa, ikkalasiga ham qo'shing.
    (re.compile(r"user_bot_to_bot_disabled", re.I), Kind.CHAT_NOT_FOUND),
    (re.compile(r"chat_id is empty", re.I), Kind.CHAT_NOT_FOUND),
    (re.compile(r"bot can'?t initiate conversation", re.I), Kind.CHAT_NOT_FOUND),
)


def _match_passive(text: str) -> str | None:
    for pattern, kind in _PASSIVE_PATTERNS:
        if pattern.search(text):
            return kind
    return None


def classify(exc: BaseException) -> Classification:
    """Istisnoni tasniflaydi. Tartib muhim: aniq turlar avval tekshiriladi."""
    text = str(exc)

    if isinstance(exc, TelegramRetryAfter):
        return Classification(
            kind=Kind.RATE_LIMIT, detail=text,
            passivate=False, retriable=True,
            retry_after=int(getattr(exc, "retry_after", 1) or 1),
        )

    if isinstance(exc, TelegramUnauthorizedError):
        return Classification(
            kind=Kind.BAD_TOKEN, detail=text,
            passivate=False, retriable=False, fatal=True,
        )

    if isinstance(exc, TelegramNetworkError):
        return Classification(kind=Kind.NETWORK, detail=text, passivate=False, retriable=True)
    if isinstance(exc, TelegramServerError):
        return Classification(kind=Kind.SERVER, detail=text, passivate=False, retriable=True)

    if any(p.search(text) for p in _FATAL_SOURCE_PATTERNS):
        return Classification(
            kind=Kind.BAD_SOURCE, detail=text, passivate=False, retriable=False, fatal=True,
        )
    if any(p.search(text) for p in _FATAL_MESSAGE_PATTERNS):
        return Classification(
            kind=Kind.BAD_MESSAGE, detail=text, passivate=False, retriable=False, fatal=True,
        )

    if isinstance(exc, (TelegramForbiddenError, TelegramBadRequest)):
        kind = _match_passive(text)
        if kind is not None:
            return Classification(kind=kind, detail=text, passivate=True, retriable=False)
        # Tanilmagan BadRequest/Forbidden — passiv QILINMAYDI, faqat yoziladi.
        return Classification(kind=Kind.UNKNOWN, detail=text, passivate=False, retriable=False)

    if isinstance(exc, TelegramAPIError):
        kind = _match_passive(text)
        if kind is not None:
            return Classification(kind=kind, detail=text, passivate=True, retriable=False)
        return Classification(kind=Kind.UNKNOWN, detail=text, passivate=False, retriable=False)

    if isinstance(exc, TimeoutError):
        return Classification(kind=Kind.NETWORK, detail=text, passivate=False, retriable=True)

    return Classification(
        kind=Kind.UNKNOWN, detail=f"{type(exc).__name__}: {text}",
        passivate=False, retriable=False,
    )
