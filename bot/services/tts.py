"""Matnni ovozga aylantirish (TTS).

Provayder: `edge-tts` — Microsoft Edge neural ovozlari. Bepul, API kalit talab
qilmaydi. Ovoz nomlari `languages.tts_voice` ustunida saqlanadi va migratsiya
002 da haqiqiy ovozlar ro'yxatidan tekshirib kiritilgan.

ky/tg/tk tillarida edge-tts da ovoz yo'q — ular uchun `supports_tts = false`,
va ovoz tugmasi umuman ko'rsatilmaydi.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

from bot.config.settings import settings

logger = logging.getLogger(__name__)


class TtsError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


@dataclass(slots=True)
class TtsResult:
    audio: bytes
    voice: str
    provider: str
    duration_ms: Optional[int]
    latency_ms: int


class EdgeTtsProvider:
    name = "edge"

    def __init__(self, timeout: int):
        self.timeout = timeout

    async def synthesize(self, text: str, voice: str) -> bytes:
        import edge_tts

        async def _run() -> bytes:
            communicate = edge_tts.Communicate(text, voice)
            buffer = bytearray()
            async for piece in communicate.stream():
                if piece["type"] == "audio":
                    buffer.extend(piece["data"])
            return bytes(buffer)

        try:
            audio = await asyncio.wait_for(_run(), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise TtsError("timeout", "TTS provayderi javob bermadi") from exc
        except Exception as exc:
            raise TtsError("provider_error", str(exc)) from exc

        if not audio:
            raise TtsError("empty_result", "Bo'sh audio")
        return audio


PROVIDERS = {"edge": EdgeTtsProvider}


class TtsService:
    """Ovoz generatsiyasi va Telegram fayl keshi.

    Telegram bir marta yuborilgan faylni `file_id` orqali qayta yuborishga ruxsat
    beradi — bu generatsiyadan ham, trafikdan ham tejaydi. Shuning uchun
    `tts_requests.telegram_file_id` saqlanadi va bir xil matn uchun qayta ishlatiladi.
    """

    def __init__(self, redis=None):
        provider_cls = PROVIDERS.get(settings.TTS_PROVIDER)
        if provider_cls is None:
            raise ValueError(f"Noma'lum TTS provayderi: {settings.TTS_PROVIDER}")
        self.provider = provider_cls(settings.TTS_TIMEOUT)
        self.redis = redis

    async def get_cached_file_id(self, key: str) -> Optional[str]:
        if not self.redis:
            return None
        try:
            value = await self.redis.get(f"tts:{key}")
            if value is None:
                return None
            return value.decode() if isinstance(value, bytes) else value
        except Exception:
            return None

    async def cache_file_id(self, key: str, file_id: str) -> None:
        if not self.redis:
            return
        try:
            # Telegram file_id'lar amalda muddatsiz, lekin 30 kun yetarli.
            await self.redis.set(f"tts:{key}", file_id, ex=30 * 86400)
        except Exception:
            pass

    async def synthesize(self, text: str, voice: str) -> TtsResult:
        if not text or not text.strip():
            raise TtsError("empty_input", "Bo'sh matn")
        if len(text) > settings.TTS_MAX_CHARS:
            raise TtsError("too_long", "Matn ovoz uchun juda uzun")
        if not voice:
            raise TtsError("no_voice", "Bu til uchun ovoz mavjud emas")

        started = time.perf_counter()
        audio = await self.provider.synthesize(text, voice)
        latency_ms = int((time.perf_counter() - started) * 1000)

        return TtsResult(
            audio=audio,
            voice=voice,
            provider=self.provider.name,
            duration_ms=None,
            latency_ms=latency_ms,
        )
