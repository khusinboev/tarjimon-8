"""Google Translate, Azure Translator, DeepL, Gemini — tashqi tarjima provayderlari.

Barchasi oddiy REST orqali (`aiohttp` bilan), `ocr.py`dagi bilan bir xil
uslubda — SDK yoki service-account fayli kerak emas, faqat API kalit
(Azure uchun mintaqa ham, `settings.AZURE_TRANSLATOR_REGION`).

**1-daraja — Google / Azure / DeepL (bepul oylik hajmi kuzatiladi).**
Har birining o'z oylik bepul BELGI hajmi bor (Google 500k, Azure 2M,
DeepL 1M — `*_FREE_MONTHLY_CHARS`), shuning uchun eng kam ishlatilgan
(hali bepul hajmidan oshmagan) kalit tanlanadi (`ocr.py`dagi ko'p kalitli
navbat bilan bir xil naqsh, faqat so'rov SONI emas, BELGI SONI bo'yicha).
Provayderlar orasida tanlov tasodifiy — talab shunday.

DeepL hamma tilni qo'llab-quvvatlamaydi (masalan amhar yo'q) — shuning
uchun har bir provayderda `supports(source, target)` bor: mos kelmasa u
nomzod bo'lmaydi va bu XATO hisoblanmaydi (circuit breaker'ga tushmaydi).

**2-daraja — Gemini (arzon, hajm kuzatilmaydi).** 1-daraja tugagach
ishlatiladi. Cheklovi BELGI hajmi emas, so'rov TEZLIGI (RPM/RPD), shu
sababli kalitlar orasida oddiy tasodifiy tanlov. 2026-09 dan sozlanmagan
(`.env`da o'chirilgan) — kerak bo'lsa kalitni qaytarish kifoya.

**Timeout:** `aiohttp.ClientTimeout` `asyncio.TimeoutError` ko'taradi, u
`aiohttp.ClientError`ning bolasi EMAS — ilgari shu sababli timeout'lar
logda BO'SH sabab bilan yozilardi (2026-09-10 gacha Gemini xatolarining
62% i). Endi har bir chaqiruvda alohida ushlanadi.
"""

from __future__ import annotations

import asyncio
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

# DeepL: `:fx` bilan tugaydigan kalit — bepul (Free) reja, alohida host.
DEEPL_FREE_URL = "https://api-free.deepl.com/v2/translate"
DEEPL_PRO_URL = "https://api.deepl.com/v2/translate"
# DeepL manba kodi oddiy ("EN"), maqsad kodi esa ba'zi tillarda variant
# talab qiladi ("EN" maqsad sifatida RAD ETILADI — EN-GB/EN-US kerak).
DEEPL_TARGET_MAP = {"en": "EN-US", "pt": "PT-BR", "zh": "ZH-HANS"}
# `/v2/languages` javobi (2026-09-11), bazadagi kodlar bilan kesishmasi.
# Amhar (am) YO'Q — bizning 4-eng katta yo'nalishimiz — u Azure/Google'da qoladi.
DEEPL_LANGS = frozenset({
    "af", "ar", "az", "be", "bg", "bn", "bs", "ca", "cs", "cy", "da", "de",
    "el", "en", "es", "et", "eu", "fa", "fi", "fr", "ga", "gl", "gu", "ha",
    "he", "hi", "hr", "ht", "hu", "hy", "id", "ig", "is", "it", "ja", "jv",
    "ka", "kk", "ko", "ky", "la", "lb", "ln", "lt", "lv", "mg", "mi", "mk",
    "ml", "mn", "mr", "ms", "mt", "my", "nb", "ne", "nl", "oc", "om", "pa",
    "pl", "ps", "pt", "qu", "ro", "ru", "sa", "sk", "sl", "sq", "sr", "st",
    "su", "sv", "sw", "ta", "te", "tg", "th", "tk", "tl", "tn", "tr", "ts",
    "tt", "uk", "ur", "uz", "vi", "wo", "xh", "yi", "zh", "zu",
})


def _timeout_error() -> RuntimeError:
    return RuntimeError(f"timeout {TIMEOUT}s")


def deepl_supports(source: str, target: str) -> bool:
    if target not in DEEPL_LANGS:
        return False
    return source == "auto" or source in DEEPL_LANGS


def _supports_all(_source: str, _target: str) -> bool:
    return True


def _period_start(period: str = "month") -> datetime:
    """Bepul hajm hisoblanadigan davr boshi: `month` (Google/Azure/DeepL)
    yoki `day` (MyMemory — kunlik limit)."""
    now = datetime.now(timezone.utc)
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def provider_key_usage(
    session: AsyncSession, provider: str, period: str = "month"
) -> dict[int, int]:
    """Joriy davrda har bir kalit necha BELGI tarjima qilganini qaytaradi.

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
            Translation.created_at >= _period_start(period),
        )
        .group_by(Translation.provider_key_index)
    )
    return {idx: total for idx, total in rows.all() if idx is not None}


async def pick_key(
    session: AsyncSession,
    provider: str,
    keys: list[str],
    free_limit_chars: int,
    period: str = "month",
) -> Optional[tuple[int, str]]:
    """Eng kam ishlatilgan, hali bepul hajmidan oshmagan kalitni tanlaydi.

    Hech biri mos kelmasa (hammasi tugagan, yoki kalit umuman yo'q) —
    `None`, chaqiruvchi keyingi provayderga yoki bepul provayderga o'tadi.
    """
    if not keys or free_limit_chars <= 0:
        return None
    usage = await provider_key_usage(session, provider, period)
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
    except asyncio.TimeoutError as exc:
        raise _timeout_error() from exc
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
    except asyncio.TimeoutError as exc:
        raise _timeout_error() from exc
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
    except asyncio.TimeoutError as exc:
        raise _timeout_error() from exc
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


async def call_deepl(text: str, source: str, target: str, api_key: str) -> str:
    """DeepL API v2. Bepul kalit (`...:fx`) alohida hostda ishlaydi."""
    url = DEEPL_FREE_URL if api_key.endswith(":fx") else DEEPL_PRO_URL
    payload: dict = {
        "text": [text],
        "target_lang": DEEPL_TARGET_MAP.get(target, target.upper()),
    }
    if source != "auto":
        payload["source_lang"] = source.upper()

    headers = {"Authorization": f"DeepL-Auth-Key {api_key}"}
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(url, json=payload, headers=headers) as response:
                status = response.status
                data = await response.json(content_type=None)
    except asyncio.TimeoutError as exc:
        raise _timeout_error() from exc
    except aiohttp.ClientError as exc:
        raise RuntimeError(redact_secrets(str(exc))) from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"DeepL noto'g'ri javob qaytardi (HTTP {status})")
    if status >= 400 or "message" in data and "translations" not in data:
        # DeepL xatoni {"message": "..."} shaklida qaytaradi (456 — oylik
        # hajm tugadi, 429 — tezlik, 403 — kalit).
        raise RuntimeError(f"DeepL HTTP {status}: {data.get('message', 'xato')}")

    translations = data.get("translations") or []
    if not translations:
        raise RuntimeError("DeepL bo'sh javob qaytardi")
    return translations[0].get("text", "")


MYMEMORY_URL = "https://api.mymemory.translated.net/get"
MYMEMORY_CODE_MAP = {"zh": "zh-CN"}
# MyMemory bitta so'rovda 500 belgidan oshiqni rad etadi ("QUERY LENGTH
# LIMIT EXCEEDED") — uzunroq matn unga umuman yuborilmaydi (`max_chars`).
MYMEMORY_MAX_CHARS = 500


async def call_mymemory(text: str, source: str, target: str, api_key: str) -> str:
    """MyMemory (translated.net) — bepul, KUNLIK limit (email bilan 50k
    belgi/kun, emailsiz 5k). `api_key` bu yerda — `de` parametri (email).

    Xatoni HTTP 200 bilan ham qaytaradi: `responseStatus` != 200 va
    matni `translatedText`da ("'AUTO' IS AN INVALID SOURCE LANGUAGE...",
    "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS...").
    Manba tili noma'lum bo'lsa `autodetect` (`auto` EMAS — rad etiladi).
    """
    src = "autodetect" if source == "auto" else MYMEMORY_CODE_MAP.get(source, source)
    params = {
        "q": text,
        "langpair": f"{src}|{MYMEMORY_CODE_MAP.get(target, target)}",
        "de": api_key,
    }
    timeout = aiohttp.ClientTimeout(total=TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.get(MYMEMORY_URL, params=params) as response:
                status = response.status
                data = await response.json(content_type=None)
    except asyncio.TimeoutError as exc:
        raise _timeout_error() from exc
    except aiohttp.ClientError as exc:
        raise RuntimeError(redact_secrets(str(exc))) from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"MyMemory noto'g'ri javob qaytardi (HTTP {status})")
    response_data = data.get("responseData") or {}
    translated = (response_data.get("translatedText") or "").strip()
    api_status = data.get("responseStatus")
    if api_status not in (200, "200") or status >= 400:
        raise RuntimeError(f"MyMemory {api_status}: {translated or data.get('responseDetails') or 'xato'}")
    if data.get("quotaFinished"):
        raise RuntimeError("MyMemory kunlik hajmi tugadi")
    if translated.upper().startswith("MYMEMORY WARNING"):
        raise RuntimeError(translated[:200])
    if not translated:
        raise RuntimeError("MyMemory bo'sh javob qaytardi")
    return translated


# ── 1-daraja: bepul hajmi kuzatiladigan provayderlar ──────────────────
# Provayder nomi -> kalitlar, bepul belgi hajmi (va davri: oy/kun), chaqiruv
# funksiyasi, til/uzunlik tekshiruvi, ustunlik. `translation.py` bularni
# bepul hajmi tugamagunicha ishlatadi.
#
#   priority 0 — asosiy sifatli provayderlar, o'zaro TASODIFIY;
#   priority 1 — faqat 0-guruh yo'q/tugagan/yiqilgan bo'lsa (MyMemory —
#                sifati pastroq, lekin `deep_translator` scraping'idan
#                ancha ishonchli va rasmiy API).
FREE_TIER_PROVIDER_CONFIG: dict[str, dict] = {
    "google_translate": {
        "label": "Google Translate",
        "keys": lambda: settings.GOOGLE_TRANSLATE_KEYS,
        "free_limit": lambda: settings.GOOGLE_TRANSLATE_FREE_MONTHLY_CHARS,
        "period": "month",
        "call": call_google,
        "supports": _supports_all,
        "max_chars": None,
        "priority": 0,
    },
    "azure_translator": {
        "label": "Azure Translator",
        "keys": lambda: settings.AZURE_TRANSLATOR_KEYS,
        "free_limit": lambda: settings.AZURE_TRANSLATOR_FREE_MONTHLY_CHARS,
        "period": "month",
        "call": call_azure,
        "supports": _supports_all,
        "max_chars": None,
        "priority": 0,
    },
    "deepl": {
        "label": "DeepL",
        "keys": lambda: settings.DEEPL_API_KEYS,
        "free_limit": lambda: settings.DEEPL_FREE_MONTHLY_CHARS,
        "period": "month",
        "call": call_deepl,
        "supports": deepl_supports,
        "max_chars": None,
        "priority": 0,
    },
    "mymemory": {
        "label": "MyMemory",
        "keys": lambda: settings.MYMEMORY_EMAILS,
        "free_limit": lambda: settings.MYMEMORY_FREE_DAILY_CHARS,
        "period": "day",
        "call": call_mymemory,
        "supports": _supports_all,
        "max_chars": MYMEMORY_MAX_CHARS,
        "priority": 1,
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
