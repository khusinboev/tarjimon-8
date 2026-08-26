"""Matn bilan ishlash yordamchilari."""

from __future__ import annotations

import hashlib
import re
from html import escape as _escape

_WHITESPACE = re.compile(r"\s+")
_SECRET_PATTERN = re.compile(r"(key|apikey|api_key|token)=[^&\s\"'<>]+", re.IGNORECASE)


def redact_secrets(text: str) -> str:
    """Xato xabaridan API kalitlarni olib tashlaydi.

    Provayder chaqiruvlari kalitni URL query-parametrida yuboradi
    (`?key=...`) — tarmoq xatosi (aiohttp'ning o'z istisnolari) ba'zan
    to'liq so'rov URL'ini xato matniga qo'shib qo'yadi, bu esa log'larga
    haqiqiy kalitni yozib qo'yishi mumkin. Har qanday tashqi xizmat
    chaqiruvidan kelgan xatoni logga/foydalanuvchiga chiqarishdan oldin
    shu orqali o'tkazish kerak.
    """
    return _SECRET_PATTERN.sub(r"\1=***", text)


def html_escape(text: str) -> str:
    """HTML parse_mode uchun xavfsiz qiladi.

    Bot global `ParseMode.HTML` bilan ishlaydi, ya'ni tarjima matnidagi `<`
    yoki `&` belgisi xabarni buzadi (Telegram teg deb o'qiydi va butun
    xabarni rad etadi). `quote=False` — qo'shtirnoq atribut ichida emas,
    matn ichida, uni almashtirish shart emas.
    """
    return _escape(text, quote=False)


def normalize(text: str) -> str:
    """Til aniqlash (langid) uchun matnni normallashtiradi.

    Bo'shliqlar va registr bo'yicha — tinish belgilari saqlanadi, chunki
    "Ket." va "Ket?" boshqacha tarjima qilinadi. DIQQAT: kesh kaliti uchun
    ISHLATILMAYDI — registrni yo'qotish "US" (mamlakat) va "us" (olmosh)
    kabi turli matnlarni bitta keshga tushirib, foydalanuvchilarga bir-
    birining natijasini qaytarib yuborardi (`content_hash`/`text_hash`ga
    qarang, ular registrni SAQLAYDI).
    """
    return _WHITESPACE.sub(" ", text).strip().lower()


def _normalize_for_hash(text: str) -> str:
    """Kesh kaliti uchun: faqat bo'shliqlar bo'yicha — registr SAQLANADI."""
    return _WHITESPACE.sub(" ", text).strip()


def content_hash(text: str, source_lang: str, target_lang: str) -> str:
    """Kesh kaliti: bir xil matn (registri bilan) + bir xil til juftligi = bir xil hash."""
    payload = f"{_normalize_for_hash(text)}\x00{source_lang}\x00{target_lang}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def text_hash(text: str, lang: str, voice: str | None = None) -> str:
    """TTS keshi uchun: matn (registri bilan) + til + ovoz."""
    payload = f"{_normalize_for_hash(text)}\x00{lang}\x00{voice or ''}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def chunk(text: str, size: int) -> list[str]:
    """Uzun matnni bo'laklarga bo'ladi, imkon qadar gap chegarasidan.

    Telegram xabar chegarasi va tarjima provayderining belgi limiti uchun.
    """
    if len(text) <= size:
        return [text]

    parts: list[str] = []
    remaining = text
    while len(remaining) > size:
        window = remaining[:size]
        # Gap oxirini qidiramiz, topilmasa — bo'shliqni, u ham bo'lmasa — kesamiz.
        cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "), window.rfind("\n"))
        if cut < size // 2:
            cut = window.rfind(" ")
        if cut < size // 2:
            cut = size
        else:
            cut += 1
        parts.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()

    if remaining:
        parts.append(remaining)
    return parts


def chunk_html_safe(text: str, limit: int) -> list[str]:
    """`chunk` kabi, lekin HTML-escape qilingandan keyingi uzunlikni hisoblaydi.

    Tarjima `<code>` ichida yuboriladi, ya'ni `&`, `<`, `>` belgilar
    `&amp;`, `&lt;`, `&gt;` ga aylanadi va matn uzayadi. Xom uzunlik bo'yicha
    bo'lash Telegram chegarasidan oshib ketishi mumkin — masalan matn ko'p
    `&` belgisidan iborat bo'lsa uzunlik 5 barobar oshadi.

    Bo'lak o'lchamini escape natijasi sig'guncha kamaytirib boradi.
    """
    size = limit
    while size > 64:
        parts = chunk(text, size)
        if all(len(html_escape(part)) <= limit for part in parts):
            return parts
        size = size * 3 // 4
    return chunk(text, size)


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(suffix)].rstrip() + suffix
