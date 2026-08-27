from functools import cached_property
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ilova sozlamalari. Manba: .env fayl yoki muhit o'zgaruvchilari."""

    # ── Telegram ──────────────────────────────────────────────
    BOT_TOKEN: str = Field(..., description="Telegram Bot Token")
    ADMIN_USER_ID: int = Field(..., description="Asosiy admin user ID")
    ADMIN_USER_IDS_RAW: Optional[str] = Field(default=None, alias="ADMIN_USER_IDS")

    @cached_property
    def ADMIN_USER_IDS(self) -> List[int]:
        # `cached_property` — bu sozlama har bir admin-filtrlangan
        # handler filtri uchun HAR BIR update'da qayta hisoblanardi
        # (CSV qayta split+parse qilinardi); `settings` global, bir marta
        # yuklanadigan singleton bo'lgani uchun keshlash xavfsiz.
        #
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

    # ── Tarjima: qo'shimcha pullik provayderlar ────────────────
    # `deep_translator` (yuqoridagi TRANSLATION_PROVIDER) — bepul, lekin
    # Google'ning veb-sahifasini scraping qilgani uchun ishonchsiz (2026-08
    # dagi bloklash voqeasi). Ikkita rasmiy pullik provayder qo'shildi —
    # ular orasida HAR BIR SO'ROVDA tasodifiy tanlanadi (ikkalasi ham
    # bepul oylik hajmidan oshmagan bo'lsa); bittasi tugasa avtomatik
    # ikkinchisiga, ikkalasi ham tugasa (yoki sozlanmagan bo'lsa) hozirgi
    # bepul provayderga davom etiladi.
    #
    # OCR.Space kabi — bir nechta kalit (har biri alohida hisob/loyiha)
    # vergul bilan berilsa, navbat bilan (kam ishlatilgani ustunlik bilan)
    # ishlatiladi. Bepul hajm HAQIQATAN ko'payishi faqat kalitlar chindan
    # ALOHIDA hisob/billing'larga tegishli bo'lsagina — buni faqat administrator
    # biladi.
    #
    #   Google Cloud Translation (v2 Basic, API kalit — service account
    #   shart emas): https://console.cloud.google.com/ -> "Cloud Translation
    #   API"ni yoqib, shu APIga cheklangan API kalit yarating.
    #   Azure Translator (v3.0): https://portal.azure.com/ -> "Translator"
    #   resursi yarating, kalit va mintaqa (region) "Keys and Endpoint"da.
    GOOGLE_TRANSLATE_API_KEYS_RAW: str = Field(default="", alias="GOOGLE_TRANSLATE_API_KEYS")
    GOOGLE_TRANSLATE_FREE_MONTHLY_CHARS: int = Field(default=500_000)

    AZURE_TRANSLATOR_KEYS_RAW: str = Field(default="", alias="AZURE_TRANSLATOR_KEYS")
    AZURE_TRANSLATOR_REGION: str = Field(default="")
    AZURE_TRANSLATOR_FREE_MONTHLY_CHARS: int = Field(default=2_000_000)

    @property
    def GOOGLE_TRANSLATE_KEYS(self) -> List[str]:
        return [k.strip() for k in self.GOOGLE_TRANSLATE_API_KEYS_RAW.split(",") if k.strip()]

    @property
    def AZURE_TRANSLATOR_KEYS(self) -> List[str]:
        return [k.strip() for k in self.AZURE_TRANSLATOR_KEYS_RAW.split(",") if k.strip()]

    # Gemini (LLM orqali tarjima) — Google/Azure'ning BEPUL hajmi tugagach
    # ishlatiladigan ARZON ikkinchi daraja (Google Cloud Translation'dan
    # taxminan 40-150 barobar arzon). Google AI Studio'dan olinadi
    # (https://aistudio.google.com/apikey) — Cloud Translation kalitidan
    # BUTUNLAY BOSHQA narsa, garchi ikkalasi ham Google bo'lsa ham.
    #
    # Google/Azure'dan farqi: oylik BELGI hajmi emas, balki so'rov TEZLIGI
    # (RPM/RPD) bilan cheklanadi — shuning uchun kalitlar orasida oylik
    # hisob emas, oddiy tasodifiy tanlov bilan yuklama taqsimlanadi.
    GEMINI_API_KEYS_RAW: str = Field(default="", alias="GEMINI_API_KEYS")
    # "latest" taxallusi — Google model nomini eskirtirib qo'ysa ham
    # (masalan "gemini-2.5-flash-lite" o'rniga "gemini-3.5-flash-lite")
    # kodni qo'lda yangilash shart bo'lmasin deb.
    GEMINI_MODEL: str = Field(default="gemini-flash-lite-latest")

    @property
    def GEMINI_API_KEYS(self) -> List[str]:
        return [k.strip() for k in self.GEMINI_API_KEYS_RAW.split(",") if k.strip()]

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
    #
    # DIQQAT: haqiqiy karta raqami/ism FAQAT `.env` orqali beriladi — bu
    # yerda sukut qiymat sifatida YOZILMAYDI (moliyaviy ma'lumot git
    # tarixida abadiy qolib ketmasligi uchun).
    DONATE_CARD_VISA: str = Field(default="")
    DONATE_CARD_UZCARD: str = Field(default="")
    DONATE_CARD_HOLDER: str = Field(default="")

    @property
    def HAS_DONATE_CARDS(self) -> bool:
        return bool(self.DONATE_CARD_VISA or self.DONATE_CARD_UZCARD)

    # ── Adminga murojaat ──────────────────────────────────────
    # Yordam bo'limida ko'rsatiladigan profil — bot orqali emas, to'g'ridan-to'g'ri
    # yozmoqchi bo'lganlar uchun. @ belgisisiz yoziladi.
    ADMIN_USERNAME: str = Field(default="adkhambek_4")
    SUPPORT_MAX_CHARS: int = Field(default=2000)

    # ── Limitlar ──────────────────────────────────────────────
    DAILY_TRANSLATION_LIMIT: int = Field(default=50)
    DAILY_TTS_LIMIT: int = Field(default=30)
    # Spam himoyasi: RATE_LIMIT_WINDOW soniyada RATE_LIMIT_REQUESTS ta so'rov.
    RATE_LIMIT_REQUESTS: int = Field(default=20)
    RATE_LIMIT_WINDOW: int = Field(default=60)

    # ── VIP (bot ichidagi premium) ─────────────────────────────
    # `User.is_premium` bilan aralashtirmaslik kerak — bu Telegram'ning o'z
    # belgisi. Bu yerdagi VIP faqat homiylik yoki referal orqali beriladi va
    # shu muddat ichida kunlik limitlar (tarjima + ovoz) cheksiz bo'ladi.
    PREMIUM_STARS_PER_DAY: int = Field(default=5, description="N stars = 1 kun VIP")
    # Bitta uzaytirishning yakuniy chegarasi — juda katta yagona homiylik
    # yillar davomida VIP bermasin (himoya, hozirgi DONATE_MAX=10000 bilan
    # birga: 10000/5=2000 kun bo'lardi, shuning uchun bu yerda kesamiz).
    PREMIUM_MAX_DAYS: int = Field(default=365)

    # ── Referal dasturi ───────────────────────────────────────
    # Bonus faqat yangi userning BIRINCHI muvaffaqiyatli tarjimasidan keyin
    # beriladi (`/start` bosilganda emas) — aks holda havolani spam qilib,
    # botdan haqiqatan foydalanmasdan mukofot yig'ish oson bo'lardi.
    REFERRAL_BONUS_DAYS: int = Field(default=3, description="taklif qilganga")
    REFERRAL_WELCOME_DAYS: int = Field(default=1, description="yangi qo'shilganga")

    # ── Limitga yetganda chegirmali VIP taklifi ────────────────
    # Umumiy "⭐ Homiylik" narxidan (PREMIUM_STARS_PER_DAY) MUSTAQIL, alohida
    # chegirmali narx — faqat kunlik limitga yetgan joyda ko'rsatiladi.
    # `donate.py` buni "quickvip" invoice payload orqali ajratadi va
    # PREMIUM_STARS_PER_DAY formulasidan o'tkazmaydi.
    QUICK_VIP_STARS: int = Field(default=10)
    QUICK_VIP_DAYS: int = Field(default=30)

    # ── Rasmdan tarjima (OCR) ──────────────────────────────────
    # Ikkala provayder ham REST orqali (aiohttp bilan) — SDK/kalit fayli
    # kerak emas. Bo'sh qoldirilsa o'sha provayder o'tkazib yuboriladi.
    #   OCR.Space: https://ocr.space/ocrapi — ro'yxatdan o'tib bepul kalit.
    #   Google Vision: GCP loyihasida "Cloud Vision API"ni yoqib, shu APIga
    #   cheklangan API kalit yaratiladi (https://console.cloud.google.com/).
    #
    # OCR.Space bepul hajmi (`OCRSPACE_FREE_MONTHLY`) HAR BIR kalit/hisobga
    # alohida beriladi. Shuning uchun bir nechta kalit (har biri alohida
    # email bilan ro'yxatdan o'tkazilgan) vergul bilan berilsa, ular
    # navbat bilan ishlatiladi — umumiy bepul hajm shuncha marta ko'payadi
    # (masalan 6 ta kalit = 6 × 25 000 = 150 000/oy). Eskilik uchun
    # `OCRSPACE_API_KEY` (birlik) ham qo'llab-quvvatlanadi.
    OCRSPACE_API_KEYS_RAW: str = Field(default="", alias="OCRSPACE_API_KEYS")
    OCRSPACE_API_KEY: str = Field(default="")
    OCRSPACE_LANGUAGE: str = Field(default="auto")
    OCRSPACE_FREE_MONTHLY: int = Field(default=25000)

    @property
    def OCRSPACE_KEYS(self) -> List[str]:
        if self.OCRSPACE_API_KEYS_RAW:
            keys = [k.strip() for k in self.OCRSPACE_API_KEYS_RAW.split(",") if k.strip()]
            if keys:
                return keys
        return [self.OCRSPACE_API_KEY] if self.OCRSPACE_API_KEY else []

    GOOGLE_VISION_API_KEY: str = Field(default="")
    GOOGLE_VISION_FREE_MONTHLY: int = Field(default=1000)

    # Rasm tarjimasi matn/ovoz limitidan BUTUNLAY ALOHIDA hisoblanadi
    # (`daily_usage.images_count`). VIP uni cheksiz emas, faqat kengaytirilgan
    # qiladi — OCR tashqi provayderga pul/hajm sarflaydi, cheksiz qilib
    # bo'lmaydi.
    DAILY_IMAGE_LIMIT_FREE: int = Field(default=3)
    DAILY_IMAGE_LIMIT_VIP: int = Field(default=15)

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
