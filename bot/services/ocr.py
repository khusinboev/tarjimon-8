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

"Rasmda matn yo'q" XATO EMAS: provayder muvaffaqiyatli ishlab bo'sh matn
qaytarsa `OcrResult.text == ""` bo'ladi, chaqiruvchi foydalanuvchiga
"matn topilmadi" deydi (`IMAGE_OCR_EMPTY` voqeasi).

Barcha urinishlar umumiy vaqt byudjetiga (`OCR_TOTAL_BUDGET`) bo'ysunadi —
foydalanuvchi har bir kalit timeout'ini ketma-ket kutib qolmasin.

Oylik hisob `translations` jadvalidan olinadi (`input_kind='photo'`,
`ocr_provider`, `ocr_key_index`, `created_at`) — alohida hisoblagich
jadvali kerak emas, chunki bu jadval baribir har bir muvaffaqiyatli
chaqiruvni yozadi.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import Translation
from bot.utils.text import redact_secrets

logger = logging.getLogger(__name__)

# Bitta provayder chaqiruvining chegarasi (soniya). Ilgari 25 edi va 3 ta
# OCR.Space kaliti + Vision ketma-ket sinalganda foydalanuvchi eng yomon
# holatda ~100 s kutib qolardi.
OCR_TIMEOUT = 20
# Barcha urinishlar uchun UMUMIY byudjet (soniya). Tugasa qolgan OCR.Space
# kalitlari sinalmaydi — timeout odatda server tomonda bo'ladi, boshqa
# kalit bilan qayta urinish uni tezlashtirmaydi.
OCR_TOTAL_BUDGET = 45
# Byudjetdan shundan kam vaqt qolsa yangi urinish boshlanmaydi.
OCR_MIN_ATTEMPT_SECONDS = 5
# OCR.Space bepul tarifi 1024 KB dan katta faylni rad etadi (E214) —
# bunday rasm OCR.Space'ga umuman yuborilmaydi, to'g'ri Vision'ga o'tiladi.
OCRSPACE_MAX_BYTES = 1024 * 1024
OCRSPACE_URL = "https://api.ocr.space/parse/image"
VISION_URL = "https://vision.googleapis.com/v1/images:annotate"

# Xato kodlari (`OcrError.code`, voqea jurnalidagi `error_code`):
#   timeout         — provayder vaqtida javob bermadi (vaqtinchalik);
#   network_error   — ulanish xatosi (vaqtinchalik);
#   provider_error  — provayder ichki xatosi (E500/E502, HTTP 5xx);
#   key_error       — AYNAN SHU kalit muammosi (yaroqsiz, limit, billing) —
#                     keyingi kalitga o'tiladi;
#   invalid_image   — rasmning o'zi rad etildi (fayl turi/hajmi);
#   config_error    — so'rov parametri noto'g'ri (masalan til kodi);
#   image_too_large — rasm OCR.Space chegarasidan katta, Vision sozlanmagan.
# Quyidagilar kalitga bog'liq EMAS — boshqa OCR.Space kaliti ham xuddi
# shunday rad etadi, shuning uchun qolgan kalitlar sinalmaydi (Vision'ga
# o'tiladi).
PER_REQUEST_CODES = frozenset({"invalid_image", "config_error"})


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
    # OCR.Space kalitlari shu oy tugab, Vision'ga sukut bo'yicha (jim)
    # o'tilgan bo'lsa True — chaqiruvchi buni ko'rib adminni ogohlantirishi
    # mumkin, aks holda OCR.Space tugagani hech qachon bilinmay qoladi.
    ocrspace_quota_exhausted: bool = False
    # Vision bepul oylik hajmidan (GOOGLE_VISION_FREE_MONTHLY) o'tib
    # ketgan bo'lsa True — Vision o'zi to'xtamaydi (pay-as-you-go bilan
    # davom etadi), lekin bundan buyon HAQIQIY xarajat boshlanadi, admin
    # buni bilishi kerak.
    vision_free_exceeded: bool = False


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


async def _vision_monthly_usage(session: AsyncSession) -> int:
    """Vision shu oy necha marta ishlatilganini qaytaradi.

    OCR.Space'dan farqli — Vision'da bitta kalit bor (kalit rotatsiyasi
    yo'q), shuning uchun GROUP BY shart emas, oddiy hisoblash yetarli.
    """
    result = await session.execute(
        select(func.count(Translation.id)).where(
            Translation.input_kind == "photo",
            Translation.ocr_provider == "google_vision",
            Translation.created_at >= _month_start(),
        )
    )
    return result.scalar_one()


def _classify_ocrspace_error(status: int, message: str) -> str:
    """OCR.Space xatosini kodga aylantiradi (izoh: `PER_REQUEST_CODES`).

    OCR.Space xatoni HTTP 200 bilan ham qaytaradi (`IsErroredOnProcessing`),
    shuning uchun HTTP holati bilan birga matn ham tekshiriladi.
    """
    lowered = message.lower()
    # Tartib muhim: E214 matnida ham "maximum" so'zi bor ("Maximum file size
    # limit 1024 KB exceeded") — rasm xatosi kalit xatosidan OLDIN tekshiriladi.
    if any(
        marker in lowered
        for marker in ("e214", "e216", "e301", "file failed validation", "file type",
                       "file extension", "size limit", "not a valid base64", "image dimensions")
    ):
        return "invalid_image"
    if status in (401, 403, 429) or any(
        marker in lowered
        for marker in ("api key", "apikey", "maximum", "rate limit", "too many", "unauthorized")
    ):
        return "key_error"
    if "e101" in lowered or "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "e201" in lowered or "invalid parameter" in lowered or "parameter" in lowered:
        return "config_error"
    return "provider_error"


def _ocrspace_error_text(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(str(m) for m in value if m)
    return str(value or "")


def parse_ocrspace_response(status: int, data: Any) -> str:
    """OCR.Space javobidan matnni ajratadi (tarmoqsiz — sinovda ham ishlatiladi).

    Muvaffaqiyatli, lekin matnsiz rasm (`FileParseExitCode == 1`, bo'sh
    `ParsedText`) — XATO EMAS, `""` qaytariladi.
    """
    if not isinstance(data, dict):
        code = "key_error" if status in (401, 403, 429) else "provider_error"
        raise OcrError(code, f"OCR.Space noto'g'ri javob qaytardi (HTTP {status})")

    results = data.get("ParsedResults") or []
    ok_pages = [r for r in results if isinstance(r, dict) and str(r.get("FileParseExitCode")) == "1"]

    if data.get("IsErroredOnProcessing") and not ok_pages:
        message = (
            _ocrspace_error_text(data.get("ErrorMessage"))
            or _ocrspace_error_text(data.get("ErrorDetails"))
            or "OCR.Space xatosi"
        )
        raise OcrError(_classify_ocrspace_error(status, message), message)
    if status >= 400:
        raise OcrError(_classify_ocrspace_error(status, ""), f"OCR.Space HTTP {status}")

    if not ok_pages:
        # Sahifa(lar) bor, lekin hech biri muvaffaqiyatli emas — sahifa
        # darajasidagi xato (masalan -10 "parse error", -20 "timeout").
        page_errors = "; ".join(
            _ocrspace_error_text(r.get("ErrorMessage")) for r in results if isinstance(r, dict)
        ).strip("; ")
        if page_errors:
            raise OcrError(_classify_ocrspace_error(status, page_errors), page_errors)
        # Umuman sahifa yo'q va xato ham yo'q — matn topilmadi deb hisoblanadi.
        return ""
    return "\n".join(r.get("ParsedText") or "" for r in ok_pages).strip()


async def _call_ocrspace(image_bytes: bytes, api_key: str) -> str:
    form = aiohttp.FormData()
    form.add_field("apikey", api_key)
    form.add_field("language", settings.OCRSPACE_LANGUAGE)
    form.add_field("OCREngine", "2")
    form.add_field("scale", "true")
    form.add_field("file", image_bytes, filename="image.jpg", content_type="image/jpeg")

    timeout = aiohttp.ClientTimeout(total=OCR_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(OCRSPACE_URL, data=form) as response:
                status = response.status
                try:
                    data = await response.json(content_type=None)
                except ValueError:
                    # 5xx'da OCR.Space ba'zan HTML sahifa qaytaradi.
                    data = None
    except asyncio.TimeoutError as exc:
        # `ClientTimeout` oddiy `asyncio.TimeoutError` ko'taradi (u
        # `ClientError` EMAS va `str()`i bo'sh) — ilgari bu umumiy
        # `except Exception`ga tushib, logda "kalit #1 ishlamadi: " deb
        # sababsiz qolardi.
        raise OcrError("timeout", f"OCR.Space {OCR_TIMEOUT} s ichida javob bermadi") from exc
    except aiohttp.ClientError as exc:
        raise OcrError("network_error", redact_secrets(str(exc)) or type(exc).__name__) from exc

    return parse_ocrspace_response(status, data)


def parse_vision_response(status: int, data: Any) -> str:
    """Vision javobidan matnni ajratadi (tarmoqsiz — sinovda ham ishlatiladi).

    Kalit/billing xatosi (yaroqsiz kalit, billing yoqilmagan, kvota) javobning
    yuqori darajasidagi `error`da keladi — ilgari u "Vision HTTP 403" deb
    sababsiz yozilardi.
    """
    data = data if isinstance(data, dict) else {}
    top_error = data.get("error")
    if isinstance(top_error, dict):
        message = redact_secrets(str(top_error.get("message") or f"Vision HTTP {status}"))
        code = "key_error" if status in (400, 401, 403, 429) else "provider_error"
        raise OcrError(code, message)

    responses = data.get("responses") or []
    if not responses:
        if status >= 400:
            code = "key_error" if status in (401, 403, 429) else "provider_error"
            raise OcrError(code, f"Vision HTTP {status}")
        raise OcrError("provider_error", "Vision bo'sh javob qaytardi")

    first = responses[0] or {}
    if "error" in first:
        error = first["error"] or {}
        message = str(error.get("message", "Vision xatosi"))
        # 3 = INVALID_ARGUMENT — rasmning o'zi yaroqsiz ("Bad image data").
        code = "invalid_image" if error.get("code") == 3 else "provider_error"
        raise OcrError(code, message)

    annotation = first.get("fullTextAnnotation")
    if annotation:
        return (annotation.get("text") or "").strip()

    text_annotations = first.get("textAnnotations") or []
    if text_annotations:
        return (text_annotations[0].get("description") or "").strip()

    # Xato yo'q, annotatsiya yo'q — rasmda matn topilmadi (xato emas).
    return ""


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
    try:
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(url, json=payload) as response:
                status = response.status
                try:
                    data = await response.json(content_type=None)
                except ValueError:
                    data = None
    except asyncio.TimeoutError as exc:
        raise OcrError("timeout", f"Vision {OCR_TIMEOUT} s ichida javob bermadi") from exc
    except aiohttp.ClientError as exc:
        # aiohttp'ning o'z istisnolari ba'zan to'liq so'rov URL'ini (kalit
        # bilan, chunki Vision faqat query-parametr orqali autentifikatsiya
        # qiladi) xato matniga qo'shadi — logga tushishidan oldin tozalanadi.
        raise OcrError("network_error", redact_secrets(str(exc)) or type(exc).__name__) from exc

    return parse_vision_response(status, data)


def _as_ocr_error(exc: BaseException) -> OcrError:
    if isinstance(exc, OcrError):
        return exc
    if isinstance(exc, asyncio.TimeoutError):
        return OcrError("timeout", "OCR vaqt byudjeti tugadi")
    return OcrError("network_error", redact_secrets(str(exc)) or type(exc).__name__)


async def _bounded(coro, deadline: float):
    """`coro`ni umumiy muddat (`deadline`, monotonic) ichida bajaradi."""
    remaining = deadline - time.monotonic()
    return await asyncio.wait_for(coro, timeout=max(remaining, 0.1))


async def extract_text(session: AsyncSession, image_bytes: bytes) -> OcrResult:
    """Rasmdan matn ajratadi, ishlagan provayder/kalitni ham qaytaradi.

    Tartib: OCR.Space kalitlaridan biri (bepul hajmidan oshmaganlari orasida
    eng kam ishlatilgani) → hammasi tugagan/xato bersa Google Vision.
    Bitta kalit xato bersa boshqasiga o'tiladi — bitta kalit muammosi
    (bloklangan, tarmoq xatosi) butun oqimni to'xtatmasin. Rasmning o'ziga
    bog'liq xato (`PER_REQUEST_CODES`) bo'lsa qolgan kalitlar sinalmaydi.

    Hammasi `OCR_TOTAL_BUDGET` ichida: Vision sozlangan bo'lsa unga bitta
    to'liq urinish vaqti (`OCR_TIMEOUT`) qoldiriladi.
    """
    ocrspace_keys = settings.OCRSPACE_KEYS
    vision_ready = bool(settings.GOOGLE_VISION_API_KEY)

    if not ocrspace_keys and not vision_ready:
        raise OcrError("not_configured", "Hech qanday OCR provayder sozlanmagan")

    started = time.monotonic()
    deadline = started + OCR_TOTAL_BUDGET
    ocrspace_deadline = deadline - (OCR_TIMEOUT if vision_ready else 0)

    last_error: Optional[OcrError] = None
    tried_ocrspace = False
    too_large_for_ocrspace = len(image_bytes) > OCRSPACE_MAX_BYTES
    # Kalitlar bor, lekin barchasi shu oy bepul hajmidan oshgani uchun
    # hech biri sinalmadi (tarmoq xatosi bilan farqi shu) — Vision jim
    # qoplab qo'ysa ham, chaqiruvchi buni bilishi kerak (pastda).
    ocrspace_quota_exhausted = False

    if ocrspace_keys and too_large_for_ocrspace:
        logger.info(
            "Rasm %s bayt — OCR.Space chegarasidan (%s) katta, o'tkazib yuboriladi",
            len(image_bytes), OCRSPACE_MAX_BYTES,
        )
    elif ocrspace_keys:
        # Hisob HAR DOIM so'raladi (limit o'chirilgan bo'lsa ham) — "eng
        # kam ishlatilgan" navbat aynan shunga tayanadi. Ilgari
        # `OCRSPACE_FREE_MONTHLY<=0` bo'lganda `usage` bo'sh `{}` qolib
        # ketardi, natijada Python'ning barqaror (stable) saralashi hamma
        # kalitni "teng" (0) deb ko'rib, doim 0-kalitdan boshlardi — ko'p
        # kalitli navbatning butun maqsadi (bepul hajmni ko'paytirish)
        # buzilardi.
        usage = await _ocrspace_key_usage(session)

        order = sorted(range(len(ocrspace_keys)), key=lambda i: usage.get(i, 0))
        if settings.OCRSPACE_FREE_MONTHLY > 0:
            order = [i for i in order if usage.get(i, 0) < settings.OCRSPACE_FREE_MONTHLY]
        ocrspace_quota_exhausted = not order

        for index in order:
            if tried_ocrspace and ocrspace_deadline - time.monotonic() < OCR_MIN_ATTEMPT_SECONDS:
                logger.warning(
                    "OCR.Space: vaqt byudjeti tugadi, qolgan kalitlar sinalmaydi"
                )
                break
            tried_ocrspace = True
            try:
                text = await _bounded(_call_ocrspace(image_bytes, ocrspace_keys[index]), ocrspace_deadline)
                return OcrResult(text=text, provider="ocrspace", key_index=index)
            except Exception as exc:
                last_error = _as_ocr_error(exc)
                logger.warning(
                    "OCR.Space kalit #%s ishlamadi [%s]: %s", index, last_error.code, last_error
                )
                if last_error.code in PER_REQUEST_CODES:
                    break

    if vision_ready:
        try:
            text = await _bounded(_call_google_vision(image_bytes), deadline)
            vision_free_exceeded = False
            if settings.GOOGLE_VISION_FREE_MONTHLY > 0:
                used_so_far = await _vision_monthly_usage(session)
                vision_free_exceeded = used_so_far >= settings.GOOGLE_VISION_FREE_MONTHLY
            return OcrResult(
                text=text, provider="google_vision", key_index=None,
                ocrspace_quota_exhausted=ocrspace_quota_exhausted,
                vision_free_exceeded=vision_free_exceeded,
            )
        except Exception as exc:
            last_error = _as_ocr_error(exc)
            logger.warning("Google Vision ishlamadi [%s]: %s", last_error.code, last_error)
    elif too_large_for_ocrspace:
        raise OcrError(
            "image_too_large",
            f"Rasm {len(image_bytes)} bayt — OCR.Space chegarasidan katta, Vision sozlanmagan",
        )
    elif ocrspace_quota_exhausted:
        # Kalitlar bor, lekin barchasi shu oy bepul hajmidan oshgan, va
        # Vision sozlanmagan — "sozlanmagan" emas, "tugagan" degani.
        raise OcrError("quota_exhausted", "OCR.Space kalitlarining barchasi oylik bepul hajmidan oshgan")

    raise last_error or OcrError("not_configured")
