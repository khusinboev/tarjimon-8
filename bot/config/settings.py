from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ilova sozlamalari. Manba: .env fayl yoki muhit o'zgaruvchilari."""

    # ── Telegram ──────────────────────────────────────────────
    BOT_TOKEN: str = Field(..., description="Telegram Bot Token")
    ADMIN_USER_ID: int = Field(..., description="Asosiy admin user ID")
    ADMIN_USER_IDS_RAW: Optional[str] = Field(default=None, alias="ADMIN_USER_IDS")

    @property
    def ADMIN_USER_IDS(self) -> List[int]:
        # Vergul bilan ajratilgan ADMIN_USER_IDS, bo'lmasa ADMIN_USER_ID.
        if self.ADMIN_USER_IDS_RAW:
            parsed: List[int] = []
            for part in self.ADMIN_USER_IDS_RAW.split(","):
                value = part.strip()
                if not value:
                    continue
                parsed.append(int(value))
            if parsed:
                return parsed
        return [self.ADMIN_USER_ID]

    # ── Ma'lumotlar bazasi ────────────────────────────────────
    POSTGRES_USER: str = Field(default="postgres")
    POSTGRES_PASSWORD: str = Field(default="postgres")
    POSTGRES_DB: str = Field(default="tarjimon8")
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432)
    DATABASE_URL_RAW: Optional[str] = Field(default=None, alias="DATABASE_URL")

    @property
    def DATABASE_URL(self) -> str:
        if self.DATABASE_URL_RAW:
            return self.DATABASE_URL_RAW
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def SYNC_DATABASE_URL(self) -> str:
        """Alembic va migratsiya skriptlari uchun — ular async ishlamaydi."""
        return self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://")

    DB_POOL_SIZE: int = Field(default=10)
    DB_MAX_OVERFLOW: int = Field(default=20)

    # ── Redis ─────────────────────────────────────────────────
    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6379)
    REDIS_DB: int = Field(default=0)
    REDIS_PASSWORD: Optional[str] = Field(default=None)

    @property
    def REDIS_URL(self) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # FSM holatlari uchun alohida Redis DB — kesh bilan aralashmasin.
    REDIS_FSM_DB: int = Field(default=1)

    @property
    def REDIS_FSM_URL(self) -> str:
        auth = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_FSM_DB}"

    # ── Tarjima ───────────────────────────────────────────────
    TRANSLATION_PROVIDER: str = Field(default="deep_translator")
    TRANSLATION_TIMEOUT: int = Field(default=15, description="soniya")
    TRANSLATION_MAX_CHARS: int = Field(default=4500)
    TRANSLATION_CACHE_TTL: int = Field(default=86400, description="soniya, 24 soat")
    DEFAULT_SOURCE_LANG: str = Field(default="auto")
    DEFAULT_TARGET_LANG: str = Field(default="uz")

    # ── TTS ───────────────────────────────────────────────────
    TTS_PROVIDER: str = Field(default="edge")
    TTS_MAX_CHARS: int = Field(default=1000)
    TTS_TIMEOUT: int = Field(default=30)

    # ── Homiylik (Telegram Stars) ─────────────────────────────
    # Stars butun sonda o'lchanadi, valyuta kodi XTR.
    DONATE_PRESETS_RAW: str = Field(default="10,25,50,100,250,500", alias="DONATE_PRESETS")
    DONATE_MIN: int = Field(default=1)
    DONATE_MAX: int = Field(default=10000)

    @property
    def DONATE_PRESETS(self) -> List[int]:
        values = []
        for part in self.DONATE_PRESETS_RAW.split(","):
            part = part.strip()
            if part.isdigit() and int(part) > 0:
                values.append(int(part))
        return values or [10, 25, 50, 100]

    # ── Homiylik: karta rekvizitlari ──────────────────────────
    # Telegram Stars hammaga qulay emas (mintaqaviy cheklovlar, Stars sotib
    # olish kerak). Karta — O'zbekistondagi foydalanuvchilar uchun oddiyroq yo'l.
    # Bo'sh qoldirilsa karta tugmasi umuman ko'rsatilmaydi.
    DONATE_CARD_VISA: str = Field(default="4413 5976 0130 3496")
    DONATE_CARD_UZCARD: str = Field(default="6262 7300 1554 9852")
    DONATE_CARD_HOLDER: str = Field(default="Ho'sinboyev Adhambek")

    @property
    def HAS_DONATE_CARDS(self) -> bool:
        return bool(self.DONATE_CARD_VISA or self.DONATE_CARD_UZCARD)

    # ── Adminga murojaat ──────────────────────────────────────
    # Yordam bo'limida ko'rsatiladigan profil — bot orqali emas, to'g'ridan-to'g'ri
    # yozmoqchi bo'lganlar uchun. @ belgisisiz yoziladi.
    ADMIN_USERNAME: str = Field(default="adkhambek_4")
    SUPPORT_MAX_CHARS: int = Field(default=2000)
    # Spam himoyasi: SUPPORT_RATE_WINDOW soniyada SUPPORT_RATE_LIMIT ta murojaat.
    # 37k foydalanuvchi bor — cheklovsiz admin chatini ko'mib tashlash mumkin.
    SUPPORT_RATE_LIMIT: int = Field(default=3)
    SUPPORT_RATE_WINDOW: int = Field(default=3600, description="soniya")

    # ── Limitlar ──────────────────────────────────────────────
    DAILY_TRANSLATION_LIMIT: int = Field(default=50)
    DAILY_TTS_LIMIT: int = Field(default=30)
    # Spam himoyasi: RATE_LIMIT_WINDOW soniyada RATE_LIMIT_REQUESTS ta so'rov.
    RATE_LIMIT_REQUESTS: int = Field(default=20)
    RATE_LIMIT_WINDOW: int = Field(default=60)

    # ── Voqealar jurnali ──────────────────────────────────────
    # "all" — har bir tugma bosish ham yoziladi (ML uchun to'liq yo'l).
    # "important" — faqat tarjima/TTS/limit/obuna/xato.
    # "off" — o'chirilgan.
    EVENT_LOG_LEVEL: str = Field(default="all")
    # Seans oynasi: shu vaqt ichidagi harakatlar bitta session_id ostida.
    SESSION_WINDOW_MINUTES: int = Field(default=30)

    # ── Ilova ─────────────────────────────────────────────────
    ENVIRONMENT: str = Field(default="production")
    LOG_LEVEL: str = Field(default="INFO")
    AUTO_CREATE_SCHEMA: bool = Field(default=False)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
