"""Rasmdan matn ajratish (OCR) — rasm tarjimasi uchun.

Ikki provayder, ikkalasi ham oddiy REST orqali (`aiohttp` bilan) — alohida
SDK yoki kalit fayli kerak emas:
  - **OCR.Space**: oyiga 25 000 ta BEPUL, bitta API kalit.
  - **Google Cloud Vision**: aniqroq, lekin oyiga faqat 1000 ta BEPUL, undan
    keyin pullik (pay-as-you-go, REST API kalit orqali — `google-cloud-vision`
    SDK/service-account JSON shart emas).

Tanlash tartibi: avval OCR.Space (bepul hajmi kattaroq), oylik bepul hajmi
tugagach Google Vision'ga o'tiladi (u pullik rejimda ham ishlashda davom
etadi — OCR.Space'ning pullik rejimi alohida obuna talab qiladi, avtomatik
davom etolmaydi). Provayderlardan biri sozlanmagan (`.env`da kalit yo'q)
bo'lsa — o'tkazib yuboriladi, ikkalasi ham yo'q bo'lsa `OcrError("not_configured")`.

Oylik hisob `translations` jadvalidan olinadi (`input_kind='photo'`,
`ocr_provider`, `created_at`) — alohida hisoblagich jadvali kerak emas,
chunki bu jadval baribir har bir muvaffaqiyatli chaqiruvni yozadi.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import aiohttp
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import Translation

logger = logging.getLogger(__name__)

OCR_TIMEOUT = 25
OCRSPACE_URL = "https://api.ocr.space/parse/image"
VISION_URL = "https://vision.googleapis.com/v1/images:annotate"


class OcrError(Exception):
    """OCR amalga oshmadi. `code` — voqea jurnaliga yoziladi."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


@dataclass(slots=True)
class OcrResult:
    text: str
    provider: str


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _monthly_count(session: AsyncSession, provider: str) -> int:
    result = await session.execute(
        select(func.count(Translation.id)).where(
            Translation.input_kind == "photo",
            Translation.ocr_provider == provider,
            Translation.created_at >= _month_start(),
        )
    )
    return result.scalar_one()


async def _call_ocrspace(image_bytes: bytes) -> str:
    form = aiohttp.FormData()
    form.add_field("apikey", settings.OCRSPACE_API_KEY)
    form.add_field("language", settings.OCRSPACE_LANGUAGE)
    form.add_field("OCREngine", "2")
    form.add_field("scale", "true")
    form.add_field("file", image_bytes, filename="image.jpg", content_type="image/jpeg")

    timeout = aiohttp.ClientTimeout(total=OCR_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        async with client.post(OCRSPACE_URL, data=form) as response:
            data = await response.json(content_type=None)

    if not isinstance(data, dict):
        raise OcrError("provider_error", "OCR.Space noto'g'ri javob qaytardi")

    if data.get("IsErroredOnProcessing"):
        message = data.get("ErrorMessage") or data.get("ErrorDetails") or "OCR.Space xatosi"
        if isinstance(message, list):
            message = "; ".join(str(m) for m in message)
        raise OcrError("provider_error", str(message))

    results = data.get("ParsedResults") or []
    if not results:
        return ""
    return "\n".join(r.get("ParsedText", "") for r in results).strip()


async def _call_google_vision(image_bytes: bytes) -> str:
    url = f"{VISION_URL}?key={settings.GOOGLE_VISION_API_KEY}"
    payload = {
        "requests": [
            {
                "image": {"content": base64.b64encode(image_bytes).decode()},
                "features": [{"type": "TEXT_DETECTION"}],
            }
        ]
    }
    timeout = aiohttp.ClientTimeout(total=OCR_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as client:
        async with client.post(url, json=payload) as response:
            data = await response.json(content_type=None)

    responses = (data or {}).get("responses") or []
    if not responses:
        raise OcrError("provider_error", "Vision bo'sh javob qaytardi")

    first = responses[0]
    if "error" in first:
        raise OcrError("provider_error", str(first["error"].get("message", "Vision xatosi")))

    annotation = first.get("fullTextAnnotation")
    if annotation:
        return (annotation.get("text") or "").strip()

    text_annotations = first.get("textAnnotations") or []
    if text_annotations:
        return (text_annotations[0].get("description") or "").strip()

    return ""


_CALLERS = {"ocrspace": _call_ocrspace, "google_vision": _call_google_vision}


async def extract_text(session: AsyncSession, image_bytes: bytes) -> OcrResult:
    """Rasmdan matn ajratadi, ishlagan provayderni ham qaytaradi.

    Tartib: OCR.Space (bepul hajmi katta) → oylik bepul hajmi tugasa yoki
    xato bersa Google Vision. Bittasi tarmoq xatosi bilan yiqilsa ham
    ikkinchisiga o'tiladi — foydalanuvchi "ishlamayapti" deb qolmasin.
    """
    ready = {
        "ocrspace": bool(settings.OCRSPACE_API_KEY),
        "google_vision": bool(settings.GOOGLE_VISION_API_KEY),
    }
    if not any(ready.values()):
        raise OcrError("not_configured", "Hech qanday OCR provayder sozlanmagan")

    prefer_ocrspace = ready["ocrspace"]
    if ready["ocrspace"] and settings.OCRSPACE_FREE_MONTHLY > 0:
        used = await _monthly_count(session, "ocrspace")
        prefer_ocrspace = used < settings.OCRSPACE_FREE_MONTHLY

    order = ("ocrspace", "google_vision") if prefer_ocrspace else ("google_vision", "ocrspace")
    order = [p for p in order if ready[p]]

    last_error: Optional[OcrError] = None
    for provider in order:
        try:
            text = await _CALLERS[provider](image_bytes)
            return OcrResult(text=text, provider=provider)
        except Exception as exc:
            logger.warning("OCR provayder %s ishlamadi: %s", provider, exc)
            last_error = exc if isinstance(exc, OcrError) else OcrError("network_error", str(exc))
            continue

    raise last_error or OcrError("not_configured")
