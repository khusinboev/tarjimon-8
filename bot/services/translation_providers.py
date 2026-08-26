"""Google Translate, Azure Translator, Gemini — pullik tarjima provayderlari.

Barchasi oddiy REST orqali (`aiohttp` bilan), `ocr.py`dagi bilan bir xil
uslubda — SDK yoki service-account fayli kerak emas, faqat API kalit
(Azure uchun mintaqa ham, `settings.AZURE_TRANSLATOR_REGION`).

**1-daraja — Google/Azure (bepul hajmi kuzatiladi).** Bir nechta kalit
sozlansa, har biri ALOHIDA hisob/loyihaga tegishli deb faraz qilinadi — har
birining o'z oylik bepul BELGI hajmi bo'ladi, shuning uchun eng kam
ishlatilgan (hali bepul hajmidan oshmagan) kalit tanlanadi (`ocr.py`dagi
ko'p kalitli navbat bilan bir xil naqsh, faqat so'rov SONI emas, BELGI
SONI bo'yicha).

**2-daraja — Gemini (arzon, hajm kuzatilmaydi).** Google/Azure'ning
BEPUL hajmi tugagach ishlatiladi — Cloud Translation'dan ~40-150 barobar
arzon (LLM narxlash siyosati tufayli), shuning uchun oylik hisob shart
emas: cheklovi BELGI hajmi emas, so'rov TEZLIGI (RPM/RPD), shu sababli
kalitlar orasida oddiy tasodifiy tanlov bilan yuklama taqsimlanadi.
"""

from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import Translation
from bot.utils.text import redact_secrets

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
    try:
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(GOOGLE_URL, params={"key": api_key}, json=payload) as response:
                status = response.status
                data = await response.json(content_type=None)
    except aiohttp.ClientError as exc:
        # aiohttp'ning o'z istisnolari ba'zan to'liq so'rov URL'ini (kalit
        # bilan) xato matniga qo'shadi — logga tushishidan oldin tozalanadi.
        raise RuntimeError(redact_secrets(str(exc))) from exc

    if not isinstance(data, dict):
        # Status kod tekshiruvi — javob tanasi kutilgan shaklda bo'lmasa
        # ham (masalan proksi/WAF xato sahifasi), HTTP xato darhol aniq
        # ko'rinadi (aynan shu turdagi xato avgust 2026'dagi deep_translator
        # voqeasiga sabab bo'lgan — u yerda esa JSON emas, HTML sahifa edi).
        raise RuntimeError(f"Google Translate noto'g'ri javob qaytardi (HTTP {status})")
    if "error" in data:
        raise RuntimeError(str((data["error"] or {}).get("message", "Google Translate xatosi")))
    if status >= 400:
        raise RuntimeError(f"Google Translate HTTP {status}")

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
    try:
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(
                AZURE_URL, params=params, headers=headers, json=[{"Text": text}]
            ) as response:
                status = response.status
                data = await response.json(content_type=None)
    except aiohttp.ClientError as exc:
        raise RuntimeError(redact_secrets(str(exc))) from exc

    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(str((data["error"] or {}).get("message", "Azure Translator xatosi")))
    if status >= 400:
        raise RuntimeError(f"Azure Translator HTTP {status}")
    if not isinstance(data, list) or not data:
        raise RuntimeError("Azure Translator bo'sh javob qaytardi")

    translations = data[0].get("translations") or []
    if not translations:
        raise RuntimeError("Azure Translator bo'sh javob qaytardi")
    return translations[0].get("text", "")


GEMINI_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_GEMINI_PROMPT = (
    "You are a translation engine embedded in a Telegram bot. Translate the "
    "text below from {source} to {target}. Reply with ONLY the raw translated "
    "text — no quotes, no markdown, no explanation, no commentary, nothing "
    "else before or after it.\n\nText:\n{text}"
)


async def call_gemini(text: str, source: str, target: str, api_key: str) -> str:
    """Gemini (LLM) orqali tarjima — alohida tarjima API emas, shuning uchun
    aniq prompt bilan faqat xom tarjimani qaytarishga majburlaymiz."""
    url = GEMINI_URL_TEMPLATE.format(model=settings.GEMINI_MODEL)
    source_label = "the auto-detected source language" if source == "auto" else source
    prompt = _GEMINI_PROMPT.format(source=source_label, target=target, text=text)
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    # Kalit URL query-parametrida emas, header'da — shunda tarmoq xatosi
    # (aiohttp'ning o'z istisnosi) so'rov URL'ini qaytarsa ham kalit unda
    # bo'lmaydi (Gemini REST API `x-goog-api-key`ni qo'llab-quvvatlaydi).
    headers = {"x-goog-api-key": api_key}
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(url, json=payload, headers=headers) as response:
                status = response.status
                data = await response.json(content_type=None)
    except aiohttp.ClientError as exc:
        raise RuntimeError(redact_secrets(str(exc))) from exc

    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(str((data["error"] or {}).get("message", "Gemini xatosi")))
    if status >= 400:
        raise RuntimeError(f"Gemini HTTP {status}")

    candidates = (data or {}).get("candidates") or []
    if not candidates:
        raise RuntimeError("Gemini bo'sh javob qaytardi (xavfsizlik filtri bo'lishi mumkin)")

    # `STOP` — normal yakun. Boshqa sabab (masalan `SAFETY`, `MAX_TOKENS`) —
    # tarjima chiqmagan, keyingi provayderga o'tish kerak.
    finish_reason = candidates[0].get("finishReason")
    if finish_reason not in (None, "STOP"):
        raise RuntimeError(f"Gemini tarjima qilmadi ({finish_reason})")

    parts = (candidates[0].get("content") or {}).get("parts") or []
    if not parts:
        raise RuntimeError("Gemini bo'sh javob qaytardi")
    return (parts[0].get("text") or "").strip()


# ── 1-daraja: bepul hajmi kuzatiladigan provayderlar ──────────────────
# Provayder nomi -> (kalitlar ro'yxati, oylik bepul belgi hajmi, chaqiruv
# funksiyasi). `translation.py` bularni bepul hajmi tugamagunicha ishlatadi.
FREE_TIER_PROVIDER_CONFIG: dict[str, dict] = {
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

# ── 2-daraja: arzon, hajmi kuzatilmaydigan provayder(lar) ─────────────
# 1-daraja tugagach/yo'q bo'lsa shu ishlatiladi. Hozircha faqat Gemini,
# lekin kelajakda boshqa arzon LLM qo'shilsa shu yerga qo'shiladi.
CHEAP_PROVIDER_CONFIG: dict[str, dict] = {
    "gemini": {
        "keys": lambda: settings.GEMINI_API_KEYS,
        "call": call_gemini,
    },
}
