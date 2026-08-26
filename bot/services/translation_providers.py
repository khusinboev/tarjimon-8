"""Google Translate va Azure Translator — pullik tarjima provayderlari.

Ikkalasi ham oddiy REST orqali (`aiohttp` bilan), `ocr.py`dagi bilan bir xil
uslubda — SDK yoki service-account fayli kerak emas, faqat API kalit
(Azure uchun mintaqa ham, `settings.AZURE_TRANSLATOR_REGION`).

Bir nechta kalit sozlansa, har biri ALOHIDA hisob/loyihaga tegishli deb
faraz qilinadi — har birining o'z oylik bepul BELGI hajmi bo'ladi, shuning
uchun eng kam ishlatilgan (hali bepul hajmidan oshmagan) kalit tanlanadi.
Bu `bot/services/ocr.py`dagi ko'p kalitli navbat bilan bir xil naqsh, faqat
so'rov SONI emas, BELGI SONI bo'yicha hisoblanadi (chunki Google/Azure
tarjima xizmatlari belgi bo'yicha to'laydi).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import aiohttp
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import Translation

TIMEOUT = 15

GOOGLE_URL = "https://translation.googleapis.com/language/translate/v2"
GOOGLE_CODE_MAP = {"zh": "zh-CN"}

AZURE_URL = "https://api.cognitive.microsofttranslator.com/translate"
AZURE_CODE_MAP = {"zh": "zh-Hans"}


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def provider_key_usage(session: AsyncSession, provider: str) -> dict[int, int]:
    """Shu oy har bir kalit necha BELGI tarjima qilganini qaytaradi.

    Faqat muvaffaqiyatli tarjimalar hisoblanadi — muvaffaqiyatsiz urinish
    provayder tarafida odatda to'lovga (billing) tushmaydi.
    """
    rows = await session.execute(
        select(
            Translation.provider_key_index,
            func.coalesce(func.sum(Translation.source_chars), 0),
        )
        .where(
            Translation.provider == provider,
            Translation.status == "success",
            Translation.created_at >= _month_start(),
        )
        .group_by(Translation.provider_key_index)
    )
    return {idx: total for idx, total in rows.all() if idx is not None}


async def pick_key(
    session: AsyncSession, provider: str, keys: list[str], free_limit_chars: int
) -> Optional[tuple[int, str]]:
    """Eng kam ishlatilgan, hali bepul hajmidan oshmagan kalitni tanlaydi.

    Hech biri mos kelmasa (hammasi tugagan, yoki kalit umuman yo'q) —
    `None`, chaqiruvchi keyingi provayderga yoki bepul provayderga o'tadi.
    """
    if not keys or free_limit_chars <= 0:
        return None
    usage = await provider_key_usage(session, provider)
    candidates = [i for i in range(len(keys)) if usage.get(i, 0) < free_limit_chars]
    if not candidates:
        return None
    best = min(candidates, key=lambda i: usage.get(i, 0))
    return best, keys[best]


async def call_google(text: str, source: str, target: str, api_key: str) -> str:
    """Google Cloud Translation — v2 Basic, API kalit bilan (service account
    shart emas)."""
    payload = {"q": text, "target": GOOGLE_CODE_MAP.get(target, target), "format": "text"}
    if source != "auto":
        payload["source"] = GOOGLE_CODE_MAP.get(source, source)

    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        async with client.post(GOOGLE_URL, params={"key": api_key}, json=payload) as response:
            data = await response.json(content_type=None)

    if not isinstance(data, dict):
        raise RuntimeError("Google Translate noto'g'ri javob qaytardi")
    if "error" in data:
        raise RuntimeError(str((data["error"] or {}).get("message", "Google Translate xatosi")))

    translations = (data.get("data") or {}).get("translations") or []
    if not translations:
        raise RuntimeError("Google Translate bo'sh javob qaytardi")
    return translations[0].get("translatedText", "")


async def call_azure(text: str, source: str, target: str, api_key: str) -> str:
    """Azure Translator — v3.0. Mintaqa (`Ocp-Apim-Subscription-Region`)
    hamma kalit uchun umumiy (`settings.AZURE_TRANSLATOR_REGION`)."""
    params = {"api-version": "3.0", "to": AZURE_CODE_MAP.get(target, target)}
    if source != "auto":
        params["from"] = AZURE_CODE_MAP.get(source, source)

    headers = {"Ocp-Apim-Subscription-Key": api_key, "Content-Type": "application/json"}
    if settings.AZURE_TRANSLATOR_REGION:
        headers["Ocp-Apim-Subscription-Region"] = settings.AZURE_TRANSLATOR_REGION

    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        async with client.post(
            AZURE_URL, params=params, headers=headers, json=[{"Text": text}]
        ) as response:
            data = await response.json(content_type=None)

    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(str((data["error"] or {}).get("message", "Azure Translator xatosi")))
    if not isinstance(data, list) or not data:
        raise RuntimeError("Azure Translator bo'sh javob qaytardi")

    translations = data[0].get("translations") or []
    if not translations:
        raise RuntimeError("Azure Translator bo'sh javob qaytardi")
    return translations[0].get("text", "")


# Provayder nomi -> (kalitlar ro'yxati, oylik bepul belgi hajmi, chaqiruv
# funksiyasi). `translation.py` shu ro'yxatni aylanib, tasodifiy tanlaydi.
# Yangi pullik provayder qo'shish uchun shu yerga bitta yozuv yetarli.
PAID_PROVIDER_CONFIG: dict[str, dict] = {
    "google_translate": {
        "keys": lambda: settings.GOOGLE_TRANSLATE_KEYS,
        "free_limit": lambda: settings.GOOGLE_TRANSLATE_FREE_MONTHLY_CHARS,
        "call": call_google,
    },
    "azure_translator": {
        "keys": lambda: settings.AZURE_TRANSLATOR_KEYS,
        "free_limit": lambda: settings.AZURE_TRANSLATOR_FREE_MONTHLY_CHARS,
        "call": call_azure,
    },
}
