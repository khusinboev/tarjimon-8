"""Matn bilan ishlash yordamchilari."""

from __future__ import annotations

import hashlib
import re

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Kesh va dedup uchun matnni normallashtiradi.

    Faqat bo'shliqlar va registr bo'yicha — tinish belgilari saqlanadi, chunki
    "Ket." va "Ket?" boshqacha tarjima qilinadi.
    """
    return _WHITESPACE.sub(" ", text).strip().lower()


def content_hash(text: str, source_lang: str, target_lang: str) -> str:
    """Kesh kaliti: bir xil matn + bir xil til juftligi = bir xil hash."""
    payload = f"{normalize(text)}\x00{source_lang}\x00{target_lang}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def text_hash(text: str, lang: str, voice: str | None = None) -> str:
    """TTS keshi uchun: matn + til + ovoz."""
    payload = f"{normalize(text)}\x00{lang}\x00{voice or ''}"
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


def truncate(text: str, limit: int, suffix: str = "…") -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(suffix)].rstrip() + suffix
