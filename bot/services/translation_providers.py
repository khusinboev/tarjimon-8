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


# Shundan katta "limit" — amalda CHEKSIZ (lokal NLLB: 10**15). Bunday
# provayder uchun oylik SUM so'rovi ma'nosiz, umuman bajarilmaydi.
UNLIMITED_CHARS = 10**12

# Hajm hisobi Redis'da shu muddat keshlanadi. Ilgari HAR tarjimada har bir
# 1-daraja provayder uchun oylik `SUM(source_chars)` bajarilardi (4–5 ta
# agregat so'rov/so'rov, oy o'tishi bilan sekinlashadigan). Endi DB'ga
# provayder boshiga daqiqada ko'pi bilan bir marta murojaat qilinadi;
# muvaffaqiyatli tarjima keshni darhol oshiradi (`record_key_usage`),
# shuning uchun kesh eskirishidan kelib chiqadigan xato — bir daqiqa
# ichida BOSHQA jarayon qilgan tarjimalar hajmi bilan cheklanadi (bir
# necha ming belgi; limitlar 500k+ bo'lgani uchun ahamiyatsiz).
USAGE_CACHE_TTL = 60
# Hash'da hech bo'lmaganda bitta maydon bo'lishi uchun (bo'sh hajm ham
# "keshlangan" deb bilinishi kerak — aks holda har safar DB'ga borilardi).
_USAGE_CACHE_MARKER = "_"


def _usage_cache_key(provider: str, period: str) -> str:
    # Davr boshi kalitda — oy/kun almashganda eski kesh o'z-o'zidan
    # ishlatilmay qoladi (TTL kutilmaydi).
    return f"tr:usage:{provider}:{period}:{_period_start(period).date().isoformat()}"


def _to_int(value) -> int:
    if isinstance(value, bytes):
        value = value.decode()
    return int(value)


async def _usage_cache_get(redis, provider: str, period: str) -> Optional[dict[int, int]]:
    try:
        raw = await redis.hgetall(_usage_cache_key(provider, period))
    except Exception:
        return None  # Redis nosoz — DB'ga tushamiz (fail-open)
    if not raw:
        return None
    usage: dict[int, int] = {}
    for field, total in raw.items():
        name = field.decode() if isinstance(field, bytes) else field
        if name == _USAGE_CACHE_MARKER:
            continue
        try:
            usage[int(name)] = _to_int(total)
        except (TypeError, ValueError):
            continue
    return usage


async def _usage_cache_set(redis, provider: str, period: str, usage: dict[int, int]) -> None:
    key = _usage_cache_key(provider, period)
    mapping = {str(idx): int(total) for idx, total in usage.items()}
    mapping[_USAGE_CACHE_MARKER] = 0
    try:
        await redis.hset(key, mapping=mapping)
        await redis.expire(key, USAGE_CACHE_TTL)
    except Exception:
        pass  # Kesh yozilmasa ham tarjima davom etadi


async def record_key_usage(redis, provider: str, index: Optional[int], chars: int, period: str = "month") -> None:
    """Muvaffaqiyatli tarjimadan keyin keshdagi hajmni optimistik oshiradi.

    Kesh YO'Q bo'lsa (muddati tugagan) hech narsa qilinmaydi — keyingi
    `pick_key` DB'dan to'liq (shu tarjimani ham o'z ichiga olgan) hisobni
    oladi. DB yozuvi handler'da keyinroq commit bo'ladi, lekin kesh 60 s
    yashaydi — oshirish ikki marta hisoblanmaydi (yangi kesh DB'dan).
    """
    if redis is None or index is None or chars <= 0:
        return
    key = _usage_cache_key(provider, period)
    try:
        if await redis.exists(key):
            await redis.hincrby(key, str(index), chars)
    except Exception:
        pass


async def pick_key(
    session: AsyncSession,
    provider: str,
    keys: list[str],
    free_limit_chars: int,
    period: str = "month",
    redis=None,
) -> Optional[tuple[int, str]]:
    """Eng kam ishlatilgan, hali bepul hajmidan oshmagan kalitni tanlaydi.

    Hech biri mos kelmasa (hammasi tugagan, yoki kalit umuman yo'q) —
    `None`, chaqiruvchi keyingi provayderga yoki bepul provayderga o'tadi.

    Limit amalda cheksiz bo'lsa (lokal model) — hisob so'ralmaydi, birinchi
    kalit qaytariladi. Aks holda hajm Redis keshidan (`USAGE_CACHE_TTL`),
    kesh yo'q/nosoz bo'lsa DB'dan olinadi.
    """
    if not keys or free_limit_chars <= 0:
        return None
    if free_limit_chars >= UNLIMITED_CHARS:
        return 0, keys[0]
    usage: Optional[dict[int, int]] = None
    if redis is not None:
        usage = await _usage_cache_get(redis, provider, period)
    if usage is None:
        usage = await provider_key_usage(session, provider, period)
        if redis is not None:
            await _usage_cache_set(redis, provider, period, usage)
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


class ProviderDeclined(Exception):
    """Provayder bu so'rovni o'zi RAD etdi (masalan lokal model tilni
    aniqlay olmadi) — bu nosozlik EMAS, circuit breaker'ga hisoblanmaydi,
    shunchaki keyingi provayderga o'tiladi."""


# Lokal servis qo'llaydigan tillar (`mt/langs.py` NLLB_CODES bilan bir xil).
LOCAL_MT_LANGS = frozenset({
    "am", "ar", "az", "de", "en", "es", "fa", "fr", "hi", "id", "it", "ja",
    "kk", "ko", "ky", "om", "ru", "tg", "tk", "tr", "uz", "zh",
})
# `auto` bilan shundan qisqa matnni servis baribir aniqlay olmaydi
# (`mt/engine.py` MIN_DETECT_CHARS) — HTTP so'rovsiz o'tkazib yuboriladi.
LOCAL_MT_MIN_AUTO_CHARS = 12


def local_mt_supports(source: str, target: str) -> bool:
    return target in LOCAL_MT_LANGS and (source == "auto" or source in LOCAL_MT_LANGS)


def interpret_local_mt_response(status: int, data) -> tuple[str, Optional[str]]:
    """Lokal servis javobini `(tarjima, model_nomi)`ga aylantiradi yoki
    istisno ko'taradi. HTTP'siz, sof funksiya — smoke_test'da tarmoqsiz
    tekshiriladi.

    Qaysi javob RAD ETISH (`ProviderDeclined`, breaker'ga tushmaydi), qaysi
    biri NOSOZLIK (`RuntimeError`, breaker'ga hisoblanadi) — farqi muhim:
    servis sog' bo'lsa-da shu matnni qila olmasligi uning yiqilgani degani
    emas. Ilgari 503 `busy` nosozlik deb hisoblanib, 120 s ichida 3 ta band
    javob breaker'ni 5 daqiqaga ochib, sog' servisni butunlay chetlab
    o'tishga olib kelardi.
    """
    if not isinstance(data, dict):
        raise RuntimeError(f"lokal MT noto'g'ri javob (HTTP {status})")
    if status in (413, 422):
        # Til aniqlanmadi / qo'llanmaydi / chiqish shubhali / juda uzun —
        # servis sog', faqat bu matnni boshqa provayder qilsin.
        raise ProviderDeclined(data.get("error") or f"HTTP {status}")
    if status == 503 and data.get("error") == "busy":
        # Navbat to'la (`mt/server.py` MAX_QUEUE) — servis sog', faqat
        # hozir band; bu so'rov boshqa provayderga ketadi.
        raise ProviderDeclined("band (navbat to'la)")
    if status >= 400:
        raise RuntimeError(f"lokal MT HTTP {status}: {data.get('error', 'xato')}")
    translated = (data.get("text") or "").strip()
    if not translated:
        raise RuntimeError("lokal MT bo'sh javob qaytardi")
    model = data.get("model")
    return translated, (str(model)[:64] if model else None)


async def call_local_mt(
    text: str, source: str, target: str, _api_key: str
) -> tuple[str, Optional[str]]:
    """Shu serverdagi lokal NLLB servisi (`mt/server.py`).

    Boshqa provayderlardan farqli `(tarjima, model)` qaytaradi — servis
    qaysi model (bazaviy NLLB yoki fine-tune) bilan ishlaganini aytadi,
    bu `translations.provider_model`ga yoziladi (sifatni model bo'yicha
    solishtirish uchun). `TranslationService._attempt` ikkala shaklni
    ham qabul qiladi.
    """
    if source == "auto" and len(" ".join(text.split())) < LOCAL_MT_MIN_AUTO_CHARS:
        raise ProviderDeclined("qisqa matn, til aniqlanmaydi")
    url = settings.LOCAL_MT_URL.rstrip("/") + "/translate"
    timeout = aiohttp.ClientTimeout(total=settings.LOCAL_MT_TIMEOUT)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(
                url, json={"text": text, "source": source, "target": target}
            ) as response:
                status = response.status
                data = await response.json(content_type=None)
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"timeout {settings.LOCAL_MT_TIMEOUT}s") from exc
    except aiohttp.ClientError as exc:
        raise RuntimeError(f"lokal MT ulanmadi: {exc}") from exc

    return interpret_local_mt_response(status, data)


# ── 1-daraja: bepul hajmi kuzatiladigan provayderlar ──────────────────
# Provayder nomi -> kalitlar, bepul belgi hajmi (va davri: oy/kun), chaqiruv
# funksiyasi, til/uzunlik tekshiruvi, ustunlik. `translation.py` bularni
# bepul hajmi tugamagunicha ishlatadi.
#
#   priority 0 — asosiy sifatli provayderlar, o'zaro TASODIFIY;
#   priority 1 — lokal NLLB (standart; `LOCAL_MT_PRIORITY` bilan o'zgaradi):
#                0-guruh yo'q/tugagan/yiqilgan bo'lsa;
#   priority 2 — MyMemory: sifati pastroq va 500 belgidan uzunini qabul
#                qilmaydi, lekin `deep_translator` scraping'idan ancha
#                ishonchli va rasmiy API.
# `priority_fn` (bo'lsa) `priority`dan ustun — sozlamadan o'qiladi.
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
        "priority": 2,
    },
    "local_mt": {
        "label": "Lokal NLLB",
        # Kalit yo'q — bitta "soxta" kalit servis yoqilganini bildiradi
        # (`pick_key`/breaker shu indeks bilan ishlaydi).
        "keys": lambda: ["local"] if settings.LOCAL_MT_URL else [],
        # `UNLIMITED_CHARS`dan katta — `pick_key` hajm so'rovini o'tkazib yuboradi.
        "free_limit": lambda: 10**15,
        "period": "month",
        "call": call_local_mt,
        "supports": local_mt_supports,
        "max_chars": None,
        "priority_fn": lambda: settings.LOCAL_MT_PRIORITY,
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
