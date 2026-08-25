"""Tarjima servisi.

Provayder: `deep-translator` (GoogleTranslator). Boshqa botlar bilan bir xil.
Servis `TranslationProvider` protokoli orqali ishlaydi — kelajakda AI provayderini
qo'shish uchun faqat yangi klass yozib, `PROVIDERS` ga qo'shish kifoya.
Baza sxemasida `provider` va `provider_model` ustunlari allaqachon bor.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from bot.config.settings import settings
from bot.utils.text import chunk, content_hash, normalize

logger = logging.getLogger(__name__)


class TranslationError(Exception):
    """Tarjima amalga oshmadi. `code` — `translations.error_code` ga yoziladi."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


# Google'ning translate.google.com sahifasi band/bloklangan bo'lsa, o'zining
# umumiy HTML xato sahifasini (masalan "Error 500 (Server Error)!!1...") qaytaradi
# — `deep-translator` buni HECH QANDAY ISTISNOSIZ, oddiy matn sifatida
# qaytaradi. Tekshirmasa, bu matn haqiqiy tarjima sifatida foydalanuvchiga
# yuboriladi (2026-08-24/25 dagi voqeada shu tarzda 71 ta foydalanuvchiga
# xato sahifa matni "tarjima" deb yuborilgan edi). Ikkala Google xato
# sahifasida ham (404, 500, ...) bir xil bo'lgan iboralar — ishonchli belgi.
#
# Diqqat: Google'ning sahifasi apostrofni tipografik ko'rinishda (’, U+2019)
# ishlatadi, oddiy ASCII (') emas — birinchi urinishda aynan shu farq
# tekshiruvni sindirgan edi (haqiqiy javob bilan qo'lda tasdiqlanmaguncha
# sezilmagan). Shuning uchun solishtirishdan oldin normallashtiramiz.
_GOOGLE_ERROR_MARKERS = (
    "that's an error",
    "that's all we know",
    "there was an error. please try again later",
)


def _looks_like_google_error_page(text: str) -> bool:
    normalized = text.lower().replace("’", "'").replace("‘", "'")
    return any(marker in normalized for marker in _GOOGLE_ERROR_MARKERS)


@dataclass(slots=True)
class TranslationResult:
    text: str
    source_lang_detected: Optional[str]
    provider: str
    provider_model: Optional[str]
    latency_ms: int
    cache_hit: bool = False


class TranslationProvider(Protocol):
    name: str

    async def translate(self, text: str, source: str, target: str) -> str: ...

    def supports(self, code: str) -> bool: ...


class DeepTranslatorProvider:
    """`deep-translator` ustidagi async o'ram.

    Kutubxona bloklovchi (sync) — har bir chaqiruv `asyncio.to_thread` da bajariladi,
    aks holda bitta sekin tarjima butun botni to'xtatib qo'yadi.
    """

    name = "deep_translator"

    # deep-translator kutayotgan kodlar bizning kodlarimizdan farq qiladigan joylar.
    CODE_MAP = {"zh": "zh-CN"}

    # Google vaqtincha band/bloklagan holatlar odatda bir necha soniyada
    # o'tib ketadi (2026-08-24/25 voqeasida qo'lda tekshirilganda ~5 urinishdan
    # 1 tasi darhol muvaffaqiyatli bo'lgan) — shuning uchun qayta urinish
    # ko'p hollarda foydalanuvchiga xato ko'rsatishning oldini oladi.
    MAX_ATTEMPTS = 3
    RETRY_DELAY = 0.6  # soniya

    def __init__(self, timeout: int):
        self.timeout = timeout

    def _map(self, code: str) -> str:
        return self.CODE_MAP.get(code, code)

    def supports(self, code: str) -> bool:
        return True

    def _translate_piece(self, translator, piece: str) -> str:
        """Bitta bo'lakni tarjima qiladi va Google'ning xato sahifasini
        haqiqiy tarjima deb qabul qilmaydi (izoh: yuqorida `_looks_like_google_error_page`).

        Ikkala nosozlik turi ham (istisno bilan yiqilish VA sukut bo'yicha
        xato sahifa qaytarish) qayta urinishga o'tkaziladi — sababi bir xil
        (Google band/bloklagan), demak davolash ham bir xil.
        """
        last_error: Exception = TranslationError("provider_error", "Noma'lum xato")
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                result = translator.translate(piece)
            except Exception as exc:
                last_error = exc
                result = None
            else:
                if result is None:
                    last_error = TranslationError("empty_result", "Provayder bo'sh javob qaytardi")
                elif _looks_like_google_error_page(result):
                    last_error = TranslationError(
                        "provider_blocked",
                        "Google xato sahifasini qaytardi (band/bloklangan bo'lishi mumkin)",
                    )
                else:
                    return result

            if attempt < self.MAX_ATTEMPTS - 1:
                time.sleep(self.RETRY_DELAY)

        raise last_error

    def _translate_sync(self, text: str, source: str, target: str) -> str:
        from deep_translator import GoogleTranslator

        translator = GoogleTranslator(source=self._map(source), target=self._map(target))

        # Provayderning belgi limiti — uzun matn bo'laklab yuboriladi.
        pieces = chunk(text, 4500)
        out = [self._translate_piece(translator, piece) for piece in pieces]
        return "\n".join(out) if len(out) > 1 else out[0]

    async def translate(self, text: str, source: str, target: str) -> str:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._translate_sync, text, source, target),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError as exc:
            raise TranslationError("timeout", "Provayder javob bermadi") from exc
        except TranslationError:
            raise
        except Exception as exc:
            message = str(exc)
            lowered = message.lower()
            if "invalid destination" in lowered or "invalid source" in lowered:
                raise TranslationError("invalid_language", message) from exc
            if "too many requests" in lowered or "429" in message:
                raise TranslationError("provider_rate_limited", message) from exc
            raise TranslationError("provider_error", message) from exc


PROVIDERS: dict[str, type] = {"deep_translator": DeepTranslatorProvider}


def detect_language(text: str) -> Optional[str]:
    """Matn tilini lokal aniqlaydi — faqat yozib qo'yish (analitika) uchun.

    Tarjimaning o'zi provayderning `auto` rejimiga tayanadi, chunki u bizning
    lokal aniqlashimizdan aniqroq. Bu funksiya `source_lang_detected` ustunini
    to'ldiradi: usiz til juftliklari statistikasi va ML uchun filtrlash mumkin emas.

    Qisqa matnda ishonchsiz, shuning uchun 12 belgidan qisqasi uchun None.
    """
    stripped = normalize(text)
    if len(stripped) < 12:
        return None
    try:
        import py3langid

        code, _score = py3langid.classify(stripped)
        return code
    except Exception:
        return None


class TranslationService:
    def __init__(self, redis=None):
        provider_cls = PROVIDERS.get(settings.TRANSLATION_PROVIDER)
        if provider_cls is None:
            raise ValueError(f"Noma'lum tarjima provayderi: {settings.TRANSLATION_PROVIDER}")
        self.provider: TranslationProvider = provider_cls(settings.TRANSLATION_TIMEOUT)
        self.redis = redis

    async def _cache_get(self, key: str) -> Optional[str]:
        if not self.redis:
            return None
        try:
            value = await self.redis.get(f"tr:{key}")
            if value is None:
                return None
            return value.decode() if isinstance(value, bytes) else value
        except Exception:
            # Kesh nosozligi tarjimani to'xtatmasligi kerak.
            return None

    async def _cache_set(self, key: str, value: str) -> None:
        if not self.redis:
            return
        try:
            await self.redis.set(f"tr:{key}", value, ex=settings.TRANSLATION_CACHE_TTL)
        except Exception:
            pass

    async def translate(self, text: str, source: str, target: str) -> TranslationResult:
        if not text or not text.strip():
            raise TranslationError("empty_input", "Bo'sh matn")
        if len(text) > settings.TRANSLATION_MAX_CHARS:
            raise TranslationError("too_long", "Matn juda uzun")
        if source == target and source != "auto":
            raise TranslationError("same_language", "Manba va maqsad til bir xil")

        started = time.perf_counter()
        key = content_hash(text, source, target)

        cached = await self._cache_get(key)
        if cached is not None:
            return TranslationResult(
                text=cached,
                source_lang_detected=detect_language(text) if source == "auto" else source,
                provider=self.provider.name,
                provider_model=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                cache_hit=True,
            )

        translated = await self.provider.translate(text, source, target)
        latency_ms = int((time.perf_counter() - started) * 1000)

        if not translated or not translated.strip():
            raise TranslationError("empty_result", "Provayder bo'sh javob qaytardi")

        await self._cache_set(key, translated)

        return TranslationResult(
            text=translated,
            source_lang_detected=detect_language(text) if source == "auto" else source,
            provider=self.provider.name,
            provider_model=None,
            latency_ms=latency_ms,
            cache_hit=False,
        )
