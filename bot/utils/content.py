"""Kelgan xabardan tarjima qilinadigan matnni ajratib olish.

Telegram xabarida matn bir necha joyda bo'lishi mumkin:

  `text`          — oddiy xabar
  `caption`       — rasm, video, hujjat, audio va h.k. ostidagi izoh
  `rich_message`  — yangi "post" formati: bloklardan iborat uzun maqola
  `poll`          — savol va variantlar
  `checklist`     — sarlavha va vazifalar

`rich_message` daraxt ko'rinishida keladi: bloklar ichida bloklar, matn esa
`RichTextUnion` — o'zi ham rekursiv (qalin ichida kursiv ichida havola...).
Shuning uchun ajratgich **tuzilishga qarab** yuradi, aniq tiplar ro'yxatiga
emas: `str` bo'lsa oladi, ro'yxat bo'lsa har biriga kiradi, `text`/`blocks`
kabi maydonlari bo'lsa ularga tushadi.

Sabab: bu tiplar hozir tez o'zgaryapti (aiogram 3.30 da 20 dan ortiq blok
turi bor va yangilari qo'shilmoqda). Qattiq ro'yxat har yangilanishda
eskirardi va yangi blokdagi matn jimgina tashlab ketilardi.
"""

from __future__ import annotations

from typing import Any, Iterator, Optional

# Ichiga kiriladigan matn maydonlari. Tartib muhim emas — hammasi yig'iladi.
_TEXT_FIELDS = (
    "text",          # paragraf, sarlavha, katak, formatlash tugunlari
    "summary",       # yig'iladigan blok sarlavhasi
    "credit",        # iqtibos muallifi
    "caption",       # media bloki izohi
    "alternative_text",  # maxsus emoji o'rnini bosuvchi matn
    "title",         # checklist sarlavhasi
)

# Ichida boshqa tugunlar bo'lgan to'plamlar.
_CHILD_FIELDS = ("blocks", "items", "cells", "tasks")

# Butunlay o'tkazib yuboriladigan tugunlar — `type` maydoni bo'yicha.
# Bulardagi matn tarjima qilinmasligi kerak:
#   pre, code               — dastur kodi, tarjima uni ishlamaydigan qiladi
#   mathematical_expression — formula
#   anchor, divider         — texnik belgilar, ko'rinadigan matni yo'q
_SKIP_TYPES = frozenset(
    {"pre", "code", "mathematical_expression", "anchor", "divider"}
)

# Ataylab OLINMAYDI:
#   label       — ro'yxat belgisi ("1", "•") — tarjima qilinadigan mazmun emas
#   language    — kod bloki tili
#   name        — anchor/reference identifikatori
#   url, username, phone_number, bank_card_number, email_address, hashtag,
#   cashtag, bot_command — bular `text` da ko'rinadigan holda ham keladi,
#   xom qiymatini tarjima qilish noto'g'ri bo'lardi


def _walk(node: Any, depth: int = 0) -> Iterator[str]:
    """Tugun ichidagi barcha matn bo'laklarini chiqaradi."""
    # Chuqurlik cheklovi: buzuq yoki halqali ma'lumot botni osib qo'ymasin.
    if node is None or depth > 24:
        return

    if isinstance(node, str):
        if node.strip():
            yield node
        return

    if isinstance(node, (list, tuple)):
        for item in node:
            yield from _walk(item, depth + 1)
        return

    node_type = getattr(node, "type", None)
    if isinstance(node_type, str) and node_type in _SKIP_TYPES:
        return
    # `type` enum bo'lishi ham mumkin.
    if node_type is not None and getattr(node_type, "value", None) in _SKIP_TYPES:
        return

    for field in _TEXT_FIELDS:
        value = getattr(node, field, None)
        if value is not None:
            yield from _walk(value, depth + 1)

    for field in _CHILD_FIELDS:
        value = getattr(node, field, None)
        if value is not None:
            yield from _walk(value, depth + 1)


def _join(parts: Iterator[str]) -> str:
    """Bo'laklarni birlashtiradi, ketma-ket takrorlarni tashlaydi.

    Formatlash tugunlari ichma-ich joylashgani uchun bir xil matn ikki marta
    chiqishi mumkin (masalan qalin ichidagi kursiv).
    """
    out: list[str] = []
    for part in parts:
        cleaned = part.strip()
        if cleaned and (not out or out[-1] != cleaned):
            out.append(cleaned)
    return "\n".join(out)


def rich_message_text(rich: Any) -> str:
    """"Post" (`rich_message`) ichidagi matn."""
    return _join(_walk(rich))


def poll_text(poll: Any) -> str:
    """Savol va variantlar — har biri alohida qatorda."""
    lines = [poll.question]
    lines.extend(option.text for option in (poll.options or []))
    return "\n".join(line for line in lines if line and line.strip())


def checklist_text(checklist: Any) -> str:
    return _join(_walk(checklist))


# Media turi → `translations.input_kind`. Izoh qaysi media ostida turganini
# bilish ML tahlili uchun foydali.
_MEDIA_KINDS = (
    ("photo", "photo"),
    ("video", "video"),
    ("document", "document"),
    ("audio", "audio"),
    ("animation", "animation"),
    ("voice", "voice"),
    ("video_note", "video_note"),
    ("paid_media", "paid_media"),
)


def media_kind(message: Any) -> str:
    for attribute, kind in _MEDIA_KINDS:
        if getattr(message, attribute, None):
            return kind
    return "caption"


def extract(message: Any) -> Optional[tuple[str, str]]:
    """Xabardan `(matn, input_kind)` ajratadi. Matn bo'lmasa `None`.

    Tartib muhim: `text` va `caption` bir vaqtda bo'lmaydi, lekin `caption`
    bilan `rich_message` birga kelishi mumkin — bunda izoh emas, postning
    o'zi asosiy mazmun.
    """
    rich = getattr(message, "rich_message", None)
    if rich is not None:
        text = rich_message_text(rich)
        if text:
            return text, "post"

    if message.text:
        return message.text, "text"

    if message.caption:
        return message.caption, media_kind(message)

    poll = getattr(message, "poll", None)
    if poll is not None:
        text = poll_text(poll)
        if text:
            return text, "poll"

    checklist = getattr(message, "checklist", None)
    if checklist is not None:
        text = checklist_text(checklist)
        if text:
            return text, "checklist"

    return None
