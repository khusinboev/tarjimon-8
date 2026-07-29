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

    def __init__(self, timeout: int):
        self.timeout = timeout

    def _map(self, code: str) -> str:
        return self.CODE_MAP.get(code, code)

    def supports(self, code: str) -> bool:
        return True

    def _translate_sync(self, text: str, source: str, target: str) -> str:
        from deep_translator import GoogleTranslator

        translator = GoogleTranslator(source=self._map(source), target=self._map(target))

        # Provayderning belgi limiti — uzun matn bo'laklab yuboriladi.
        pieces = chunk(text, 4500)
        out = []
        for piece in pieces:
            result = translator.translate(piece)
            # Ishlab turgan botdagi `'NoneType' has no attribute 'lower'` xatosining
            # sababi shu: provayder ba'zan None qaytaradi va uni tekshirilmagan.
            if result is None:
                raise TranslationError("empty_result", "Provayder bo'sh javob qaytardi")
            out.append(result)
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
