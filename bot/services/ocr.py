"""Rasmdan matn ajratish (OCR) — rasm tarjimasi uchun.

Ikki provayder, ikkalasi ham oddiy REST orqali (`aiohttp` bilan) — alohida
SDK yoki kalit fayli kerak emas:
  - **OCR.Space**: har bir kalit/hisobga oyiga 25 000 ta BEPUL. Bir nechta
    kalit (`settings.OCRSPACE_KEYS`, har biri alohida email bilan
    ro'yxatdan o'tkazilgan hisob) sozlansa, navbat bilan — kam ishlatilgani
    ustunlik bilan — ishlatiladi, ya'ni umumiy bepul hajm N marta ko'payadi.
  - **Google Cloud Vision**: aniqroq, lekin oyiga faqat 1000 ta BEPUL, undan
    keyin pullik (pay-as-you-go, REST API kalit orqali — `google-cloud-vision`
    SDK/service-account JSON shart emas).

Tanlash tartibi: avval OCR.Space kalitlaridan biri (bepul hajmidan
oshmaganlari orasida eng kam ishlatilgani), hammasi tugagach yoki xato
bersa Google Vision'ga o'tiladi (u pullik rejimda ham ishlashda davom
etadi — OCR.Space'ning pullik rejimi alohida obuna talab qiladi, avtomatik
davom etolmaydi). Provayder/kalit sozlanmagan bo'lsa o'tkazib yuboriladi,
hech biri yo'q/hammasi tugagan bo'lsa `OcrError` ko'tariladi.

Oylik hisob `translations` jadvalidan olinadi (`input_kind='photo'`,
`ocr_provider`, `ocr_key_index`, `created_at`) — alohida hisoblagich
jadvali kerak emas, chunki bu jadval baribir har bir muvaffaqiyatli
chaqiruvni yozadi.
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
    key_index: Optional[int] = None


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def _ocrspace_key_usage(session: AsyncSession) -> dict[int, int]:
    """Har bir OCR.Space kaliti shu oy necha marta ishlatilganini qaytaradi.

    Bitta GROUP BY so'rov — kalitlar soni qancha bo'lmasin bir marta ishlaydi.
    """
    rows = await session.execute(
        select(Translation.ocr_key_index, func.count(Translation.id))
        .where(
            Translation.input_kind == "photo",
            Translation.ocr_provider == "ocrspace",
            Translation.created_at >= _month_start(),
        )
        .group_by(Translation.ocr_key_index)
    )
    return {index: count for index, count in rows.all() if index is not None}


async def _call_ocrspace(image_bytes: bytes, api_key: str) -> str:
    form = aiohttp.FormData()
    form.add_field("apikey", api_key)
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


async def extract_text(session: AsyncSession, image_bytes: bytes) -> OcrResult:
    """Rasmdan matn ajratadi, ishlagan provayder/kalitni ham qaytaradi.

    Tartib: OCR.Space kalitlaridan biri (bepul hajmidan oshmaganlari orasida
    eng kam ishlatilgani) → hammasi tugagan/xato bersa Google Vision.
    Bitta kalit xato bersa boshqasiga o'tiladi — bitta kalit muammosi
    (bloklangan, tarmoq xatosi) butun oqimni to'xtatmasin.
    """
    ocrspace_keys = settings.OCRSPACE_KEYS
    vision_ready = bool(settings.GOOGLE_VISION_API_KEY)

    if not ocrspace_keys and not vision_ready:
        raise OcrError("not_configured", "Hech qanday OCR provayder sozlanmagan")

    last_error: Optional[OcrError] = None
    tried_ocrspace = False

    if ocrspace_keys:
        usage: dict[int, int] = {}
        if settings.OCRSPACE_FREE_MONTHLY > 0:
            usage = await _ocrspace_key_usage(session)

        order = sorted(range(len(ocrspace_keys)), key=lambda i: usage.get(i, 0))
        if settings.OCRSPACE_FREE_MONTHLY > 0:
            order = [i for i in order if usage.get(i, 0) < settings.OCRSPACE_FREE_MONTHLY]

        for index in order:
            tried_ocrspace = True
            try:
                text = await _call_ocrspace(image_bytes, ocrspace_keys[index])
                return OcrResult(text=text, provider="ocrspace", key_index=index)
            except Exception as exc:
                logger.warning("OCR.Space kalit #%s ishlamadi: %s", index, exc)
                last_error = exc if isinstance(exc, OcrError) else OcrError("network_error", str(exc))
                continue

    if vision_ready:
        try:
            text = await _call_google_vision(image_bytes)
            return OcrResult(text=text, provider="google_vision", key_index=None)
        except Exception as exc:
            logger.warning("Google Vision ishlamadi: %s", exc)
            last_error = exc if isinstance(exc, OcrError) else OcrError("network_error", str(exc))
    elif ocrspace_keys and not tried_ocrspace:
        # Kalitlar bor, lekin barchasi shu oy bepul hajmidan oshgan, va
        # Vision sozlanmagan — "sozlanmagan" emas, "tugagan" degani.
        raise OcrError("quota_exhausted", "OCR.Space kalitlarining barchasi oylik bepul hajmidan oshgan")

    raise last_error or OcrError("not_configured")
