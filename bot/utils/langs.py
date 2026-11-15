"""Foydalanuvchi til sozlamalari bilan bog'liq sof yordamchilar."""

from __future__ import annotations

from bot.config.settings import settings


def effective_target_lang(source: str, target: str) -> str:
    """Tarjima uchun haqiqiy maqsad til.

    `auto` faqat MANBA til bo'la oladi. Eski botda (2026-07 gacha) maqsad tilni
    ham `auto` qilish mumkin edi; u amalda inglizcha degani edi (tarixiy
    tarjimalar: o'zbekcha/indonezcha -> inglizcha, inglizcha -> o'zgarishsiz).
    Yangi botda bunday so'rov har safar yiqilardi (MyMemory 403 "'AUTO' IS AN
    INVALID TARGET LANGUAGE"). Migratsiya 019 sozlamalarni tuzatadi; bu esa
    qolib ketgan yoki keshdagi qiymat uchun himoya.
    """
    if target != "auto":
        return target
    return settings.DEFAULT_TARGET_LANG if source == "en" else "en"
