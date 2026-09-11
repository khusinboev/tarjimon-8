"""Tarjima servisi — uch DARAJALI (tiered) provayder zanjiri.

1-daraja: Google Cloud Translation / Azure Translator — bepul oylik BELGI
hajmi hali tugamagan bo'lsa, ular orasidan TASODIFIY tanlanadi (rasmiy API,
pullik, lekin bepul hajmi bor).

2-daraja: Gemini — 1-daraja tugagan/yo'q bo'lsa ishlatiladi. Cloud
Translation'dan ~40-150 barobar arzon, lekin cheklovi hajm emas so'rov
tezligi (RPM/RPD) bo'lgani uchun oylik hisob shart emas — kalitlar orasida
oddiy tasodifiy tanlov.

3-daraja: `deep_translator` — bepul, lekin Google veb-sahifasini scraping
qilgani uchun ishonchsiz oxirgi zaxira. Yuqoridagi ikkala daraja ham
yo'q/tugagan/xato bersa shunga qaytiladi.

Batafsil: `bot/services/translation_providers.py`. Baza sxemasida
`provider`/`provider_model`/`provider_key_index` ustunlari allaqachon bor.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.services import error_rate
from bot.services.admin_alerts import alert_admins_once
from bot.services.circuit_breaker import OPEN_SECONDS, CircuitBreaker
from bot.services.translation_providers import (
    CHEAP_PROVIDER_CONFIG,
    FREE_TIER_PROVIDER_CONFIG,
    pick_key,
)
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
    provider_key_index: Optional[int] = None


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
    def __init__(self, redis=None, bot: Optional[Bot] = None):
        provider_cls = PROVIDERS.get(settings.TRANSLATION_PROVIDER)
        if provider_cls is None:
            raise ValueError(f"Noma'lum tarjima provayderi: {settings.TRANSLATION_PROVIDER}")
        # Har doim mavjud, oxirgi zaxira — pullik provayderlar yo'q/tugagan/
        # xato bersa shunga qaytiladi.
        self.deep_provider: TranslationProvider = provider_cls(settings.TRANSLATION_TIMEOUT)
        self.redis = redis
        # Faqat kvota/limit ogohlantirishlarini adminga yuborish uchun —
        # tarjimaning o'ziga ta'siri yo'q, `bot=None` bo'lsa ogohlantirish
        # jim o'tkaziladi (`alert_admins_once`).
        self.bot = bot
        # Ketma-ket yiqilayotgan provayder/kalitni vaqtincha chetlab o'tish
        # (izoh: `circuit_breaker.py`). Redis orqali jarayonlar o'rtasida umumiy.
        self.breaker = CircuitBreaker(redis)

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

    async def _pick_free_tier_candidates(
        self, session: AsyncSession, source: str, target: str, text_len: int = 0
    ) -> list[tuple[str, int, str]]:
        """Hali bepul hajmidan oshmagan 1-daraja provayder+kalit variantlari.

        Ustunlik guruhlari (`priority`) bo'yicha: 0-guruh (Google/Azure/DeepL)
        o'zaro TASODIFIY tartibda birinchi — talab shunday: "toki limiti
        tugagunicha random"; 1-guruh (MyMemory) faqat ulardan keyin.

        Filtrlar (hech biri XATO hisoblanmaydi, breaker'ga tushmaydi):
          - provayder shu til juftligini qo'llab-quvvatlaydimi (DeepL'da amhar yo'q);
          - matn provayder chegarasidan uzun emasmi (MyMemory — 500 belgi);
          - circuit breaker ochiq emasmi (yaqinda ketma-ket yiqilgan kalit
            vaqtincha chetlanadi — buzilgan Google kaliti har so'rovga
            1 soniya qo'shmasin).
        """
        groups: dict[int, list[tuple[str, int, str]]] = {}
        for name, cfg in FREE_TIER_PROVIDER_CONFIG.items():
            keys = cfg["keys"]()
            if not keys:
                continue
            if not cfg["supports"](source, target):
                continue
            max_chars = cfg.get("max_chars")
            if max_chars and text_len > max_chars:
                continue
            picked = await pick_key(
                session, name, keys, cfg["free_limit"](), cfg.get("period", "month")
            )
            if picked is None:
                period_label = "bugun" if cfg.get("period") == "day" else "bu oy"
                await alert_admins_once(
                    self.bot, self.redis, f"quota_exhausted:{name}",
                    f"⚠️ <b>{cfg['label']}</b>ning barcha kalitlari {period_label} bepul "
                    "hajmidan oshdi — qolgan provayderlar/zaxira ishlatilmoqda.",
                )
                continue
            index, api_key = picked
            if await self.breaker.is_open(name, index):
                continue
            groups.setdefault(cfg.get("priority", 0), []).append((name, index, api_key))

        candidates: list[tuple[str, int, str]] = []
        for priority in sorted(groups):
            group = groups[priority]
            random.shuffle(group)
            candidates.extend(group)
        return candidates

    async def _pick_cheap_tier_candidates(self) -> list[tuple[str, int, str]]:
        """2-daraja (Gemini va h.k.) — hajm kuzatilmaydi, faqat sozlangan
        kalitlar orasida tasodifiy tartib (breaker ochiqlari chetlanadi)."""
        candidates: list[tuple[str, int, str]] = []
        for name, cfg in CHEAP_PROVIDER_CONFIG.items():
            keys = cfg["keys"]()
            for index, api_key in enumerate(keys):
                if await self.breaker.is_open(name, index):
                    continue
                candidates.append((name, index, api_key))
        random.shuffle(candidates)
        return candidates

    async def _attempt(
        self, name: str, index: int, api_key: str, call, text: str, source: str, target: str
    ) -> tuple[Optional[str], Optional[str]]:
        """Bitta provayder+kalitni sinaydi: `(tarjima, None)` yoki `(None, xato)`.

        Breaker hisobini shu yerda yuritadi. Log'da xato matni bo'sh bo'lsa
        (masalan ba'zi istisnolar `str()`da bo'sh) — tur nomi yoziladi;
        ilgari shu sababli timeout'lar "ishlamadi: " deb sababsiz qolardi.
        """
        try:
            translated = await call(text, source, target, api_key)
        except Exception as exc:
            reason = str(exc) or type(exc).__name__
            logger.warning("%s (kalit #%s) ishlamadi: %s", name, index, reason)
            if await self.breaker.record_failure(name, index):
                logger.warning(
                    "%s (kalit #%s) ketma-ket yiqildi — %s soniya chetlab o'tiladi",
                    name, index, OPEN_SECONDS,
                )
                await alert_admins_once(
                    self.bot, self.redis, f"circuit_open:{name}:{index}",
                    f"⛔ <b>{name}</b> (kalit #{index}) ketma-ket xato qaytarmoqda — "
                    f"vaqtincha chetlab o'tilmoqda. Oxirgi xato: {reason[:200]}",
                )
            return None, reason
        if translated and translated.strip():
            await self.breaker.record_success(name, index)
            return translated, None
        logger.warning("%s (kalit #%s) bo'sh javob qaytardi", name, index)
        await self.breaker.record_failure(name, index)
        return None, "bo'sh javob qaytardi"

    async def _dispatch(
        self, session: AsyncSession, text: str, source: str, target: str
    ) -> tuple[str, str, Optional[int]]:
        """Uch daraja: 1) Google/Azure/DeepL (bepul hajmi tugamagan bo'lsa,
        tasodifiy), 2) Gemini (arzon, tasodifiy kalit), 3) `deep_translator`
        (oxirgi, bepul-lekin-ishonchsiz zaxira).

        1-darajada BIR nechta provayder bor — bittasi xato bersa qolganlari
        sinab ko'riladi; hammasi bo'sh/xato bo'lsagina pastga tushiladi.
        """
        free_candidates = await self._pick_free_tier_candidates(session, source, target, len(text))
        last_free_error: Optional[str] = None
        last_free_provider: Optional[str] = None
        for name, index, api_key in free_candidates:
            translated, error = await self._attempt(
                name, index, api_key, FREE_TIER_PROVIDER_CONFIG[name]["call"],
                text, source, target,
            )
            if translated is not None:
                return translated, name, index
            last_free_error, last_free_provider = error, name

        # 1-daraja nomzodlari BOR edi (hajmi ham tugamagan — aks holda
        # `_pick_free_tier_candidates` allaqachon o'z alertini yuborgan
        # bo'lardi), lekin HAMMASI xato qaytardi — bu kvota tugashidan
        # BUTUNLAY boshqa muammo (masalan provayder API'si o'zi ishlamay
        # qolgan), cheap-tier bilan bir xil izchillikda alert beriladi.
        if free_candidates and last_free_error is not None:
            provider_label = last_free_provider or free_candidates[0][0]
            await alert_admins_once(
                self.bot, self.redis, f"free_tier_failed:{provider_label}",
                f"🚨 <b>{provider_label}</b> orqali tarjima ishlamayapti (hajmi "
                f"hali tugamagan) — barcha kalitlar xato qaytardi. Oxirgi "
                f"xato: {last_free_error}\nKeyingi bosqichga o'tilmoqda.",
            )

        cheap_candidates = await self._pick_cheap_tier_candidates()
        last_cheap_error: Optional[str] = None
        last_cheap_provider: Optional[str] = None
        for name, index, api_key in cheap_candidates:
            translated, error = await self._attempt(
                name, index, api_key, CHEAP_PROVIDER_CONFIG[name]["call"],
                text, source, target,
            )
            if translated is not None:
                return translated, name, index
            last_cheap_error, last_cheap_provider = error, name

        # 2-daraja sozlangan edi (kalitlari bor), lekin BARCHA kalitlari
        # ishlamadi — bu tasodifiy bitta xato emas, e'tibor talab qiladi
        # (masalan kvota/RPM chegarasi yoki kalit bekor qilingan).
        if cheap_candidates and last_cheap_error is not None:
            provider_label = last_cheap_provider or cheap_candidates[0][0]
            await alert_admins_once(
                self.bot, self.redis, f"cheap_tier_failed:{provider_label}",
                f"🚨 <b>{provider_label}</b> orqali tarjima ishlamayapti — "
                f"barcha kalitlar xato qaytardi. Oxirgi xato: {last_cheap_error}\n"
                "Hozircha bepul (ishonchsiz) zaxiraga tushilmoqda.",
            )

        try:
            translated = await self.deep_provider.translate(text, source, target)
        except TranslationError as exc:
            # Oxirgi (3-daraja) zaxira ham ishlamadi — tarjima UMUMAN
            # ishlamayapti, foydalanuvchiga xato ko'rsatilishidan oldin
            # adminga xabar berish kerak (aks holda buni faqat shikoyatdan
            # bilib qolamiz).
            await alert_admins_once(
                self.bot, self.redis, "all_tiers_down",
                "🆘 <b>Tarjima BUTUNLAY ishlamayapti</b> — barcha uch daraja "
                f"(Google/Azure, Gemini, deep_translator) xato qaytardi. "
                f"Oxirgi xato: {exc.code} — {exc}",
            )
            raise
        return translated, self.deep_provider.name, None

    async def translate(
        self, session: AsyncSession, text: str, source: str, target: str
    ) -> TranslationResult:
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
            # Latency har ikkala yo'lda (kesh/haqiqiy chaqiruv) BIR XIL
            # nuqtada o'lchanadi — til aniqlashdan OLDIN — aks holda
            # kesh-hit va kesh-miss latency'lari turli narsani anglatib,
            # dashboard'larni chalg'itardi.
            latency_ms = int((time.perf_counter() - started) * 1000)
            detected = await asyncio.to_thread(detect_language, text) if source == "auto" else source
            return TranslationResult(
                text=cached,
                source_lang_detected=detected,
                provider="cache",
                provider_model=None,
                latency_ms=latency_ms,
                cache_hit=True,
            )

        # Natija statistikasi (`error_rate`) — provayderdan qat'i nazar,
        # foydalanuvchi tarjima OLDIMI yoki YO'QMI. Xato ulushi oshsa admin
        # 15 daqiqa ichida biladi (2026-09-04…10 dagi bir haftalik ko'rlik
        # takrorlanmasin).
        try:
            translated, provider_name, key_index = await self._dispatch(session, text, source, target)
        except TranslationError:
            await error_rate.record(self.redis, ok=False)
            await error_rate.check_and_alert(self.bot, self.redis)
            raise
        await error_rate.record(self.redis, ok=True)
        latency_ms = int((time.perf_counter() - started) * 1000)

        if not translated or not translated.strip():
            raise TranslationError("empty_result", "Provayder bo'sh javob qaytardi")

        await self._cache_set(key, translated)
        # `py3langid.classify` CPU-bog'liq (sync) — event loop'ni
        # bloklamasin deb alohida oqimda ishga tushiriladi.
        detected = await asyncio.to_thread(detect_language, text) if source == "auto" else source

        return TranslationResult(
            text=translated,
            source_lang_detected=detected,
            provider=provider_name,
            provider_model=None,
            provider_key_index=key_index,
            latency_ms=latency_ms,
            cache_hit=False,
        )
