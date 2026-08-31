"""SQLAlchemy modellar.

Loyihalash qoidalari (batafsil: SXEMA.md):
  - `translations` va `events` — append-only fakt jadvallari. UPDATE/DELETE qilinmaydi.
  - Har bir jadvalda `meta` (JSONB) — yangi maydonni migratsiyasiz saqlash uchun.
  - Enum'lar VARCHAR + CHECK. PostgreSQL ENUM'ga qiymat qo'shish jadvalni qulflaydi.
  - Barcha FK `users.id` ga qaraydi, `users.telegram_id` ga emas.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


def _meta():
    """Har bir jadvalning kengaytirish nuqtasi."""
    return Column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))


def _created_at(index: bool = False):
    return Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=index,
    )


# ─────────────────────────────────────────────────────────────
#  Blok 1 — Foydalanuvchi
# ─────────────────────────────────────────────────────────────

USER_STATUSES = ("active", "blocked_bot", "banned", "deleted")
USER_ROLES = ("user", "admin", "owner")


class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)

    username = Column(String(255))
    first_name = Column(String(255))
    last_name = Column(String(255))
    telegram_lang = Column(String(10))
    is_premium = Column(Boolean, default=False, server_default=sql_text("false"), nullable=False)

    status = Column(String(20), default="active", server_default=sql_text("'active'"), nullable=False, index=True)
    role = Column(String(20), default="user", server_default=sql_text("'user'"), nullable=False)

    # /start dagi deep-link parametri — referal manbaini kuzatish uchun.
    source = Column(String(64))
    referred_by = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True)

    created_at = _created_at(index=True)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_seen_at = _created_at(index=True)
    blocked_at = Column(DateTime(timezone=True))
    meta = _meta()

    settings = relationship(
        "UserSettings", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    referrer = relationship("User", remote_side=[id], backref="referrals")

    __table_args__ = (
        CheckConstraint(
            "status IN " + str(USER_STATUSES), name="ck_users_status"
        ),
        CheckConstraint("role IN " + str(USER_ROLES), name="ck_users_role"),
    )


class UserSettings(Base):
    """Users bilan 1:1. Alohida jadval — tez-tez o'zgaradi, `users` ni shishirmasin."""

    __tablename__ = "user_settings"

    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )

    source_lang = Column(String(10), default="auto", server_default=sql_text("'auto'"), nullable=False)
    target_lang = Column(String(10), default="uz", server_default=sql_text("'uz'"), nullable=False)
    # Standart `en` — `bot/locales.DEFAULT` bilan bir xil. Haqiqiy qiymat
    # user yaratilganda `locales.resolve(telegram_lang)` dan keladi.
    interface_lang = Column(String(10), default="en", server_default=sql_text("'en'"), nullable=False)

    tts_enabled = Column(Boolean, default=True, server_default=sql_text("true"), nullable=False)
    tts_auto = Column(Boolean, default=False, server_default=sql_text("false"), nullable=False)
    tts_voice = Column(String(64))

    # Ikki alohida flag: birinchisi userga tarixni ko'rsatish/ko'rsatmaslik,
    # ikkinchisi ML eksportiga tushish/tushmaslik. Ular bir narsa emas.
    save_history = Column(Boolean, default=True, server_default=sql_text("true"), nullable=False)
    allow_training = Column(Boolean, default=True, server_default=sql_text("true"), nullable=False, index=True)

    daily_limit_override = Column(Integer)
    tts_limit_override = Column(Integer)
    image_limit_override = Column(Integer)

    # Bot ichidagi VIP (Telegram Premium bilan aralashtirmaslik kerak —
    # `User.is_premium` Telegram'ning o'z belgisi). Homiylik yoki referal
    # orqali qo'lga kiritiladi: shu vaqtgacha kunlik limitlar cheksiz.
    premium_until = Column(DateTime(timezone=True))

    # Referal bonusi FAQAT yangi userning birinchi muvaffaqiyatli
    # tarjimasidan keyin beriladi (`/start` bosilganda emas) — aks holda
    # havolani haqiqiy foydalanmasdan spam qilib mukofot yig'ish oson bo'lardi.
    # Bu bayroq bonusni ikki marta bermaslik uchun.
    referral_bonus_granted = Column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )

    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    meta = _meta()

    user = relationship("User", back_populates="settings")


class Language(Base):
    __tablename__ = "languages"

    code = Column(String(10), primary_key=True)
    name_uz = Column(String(64), nullable=False)
    name_en = Column(String(64), nullable=False)
    name_native = Column(String(64))
    flag = Column(String(16))

    supports_tts = Column(Boolean, default=False, server_default=sql_text("false"), nullable=False)
    supports_detection = Column(Boolean, default=True, server_default=sql_text("true"), nullable=False)
    tts_voice = Column(String(64))

    is_active = Column(Boolean, default=True, server_default=sql_text("true"), nullable=False, index=True)
    sort_order = Column(Integer, default=100, server_default=sql_text("100"), nullable=False)
    meta = _meta()


# ─────────────────────────────────────────────────────────────
#  Blok 2 — Tarjima (ML uchun asosiy qiymat)
# ─────────────────────────────────────────────────────────────

CHAT_TYPES = ("private", "group", "supergroup", "channel", "inline")
# Matn qayerdan kelgani. Media izohi uchun umumiy "caption" emas, aniq media
# turi yoziladi — qaysi kontent ostidagi izoh ekani ML tahlili uchun muhim.
INPUT_KINDS = (
    "text", "voice", "photo", "document", "forward", "inline",
    "caption", "video", "audio", "animation", "video_note", "paid_media",
    "post", "poll", "checklist",
)
TRANSLATION_STATUSES = ("success", "error", "timeout")


class Translation(Base):
    """Append-only. Har bir qator = parallel korpus juftligi.

    Bu jadval loyihaning eng qimmatli aktivi — qayta yig'ib bo'lmaydigan ma'lumot.
    Shu sababli hech qachon UPDATE qilinmaydi; sevimli/feedback holati
    `translation_signals` da alohida yoziladi.
    """

    __tablename__ = "translations"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True)

    chat_id = Column(BigInteger)
    chat_type = Column(String(20), default="private", server_default=sql_text("'private'"), nullable=False)
    input_kind = Column(String(20), default="text", server_default=sql_text("'text'"), nullable=False)

    # `chat_id` bilan birga manba xabarning ANIQ manzili — keyinchalik
    # Telegram'dagi aynan o'sha xabarga qaytib borish uchun.
    source_message_id = Column(BigInteger)

    # Media manbasi — `input_kind` matn bo'lmaganda to'ldiriladi.
    # `media_file_id` bilan faylni qayta yuklab olish mumkin,
    # `media_file_unique_id` esa o'zgarmas va faqat u orqali "bu bir xil
    # faylmi?" aniqlanadi (batafsil: `bot/utils/media.py`).
    media_file_id = Column(Text)
    media_file_unique_id = Column(String(64))
    media_mime_type = Column(String(128))
    media_file_size = Column(BigInteger)
    media_file_name = Column(Text)

    source_lang_requested = Column(String(10), default="auto", server_default=sql_text("'auto'"), nullable=False)
    source_lang_detected = Column(String(10))
    target_lang = Column(String(10), nullable=False)

    source_text = Column(Text, nullable=False)
    target_text = Column(Text)

    # SHA-256(normallashtirilgan matn + til juftligi) — kesh qidiruvi va dedup.
    source_hash = Column(String(64), index=True)
    source_chars = Column(Integer, default=0, server_default=sql_text("0"), nullable=False)
    target_chars = Column(Integer, default=0, server_default=sql_text("0"), nullable=False)

    provider = Column(String(32), nullable=False)
    provider_model = Column(String(64))
    # `provider` shu tarjimani BAJARGAN dvigatel: `deep_translator`,
    # `google_translate`, `azure_translator` yoki keshdan olingan bo'lsa
    # `cache`. `google_translate`/`azure_translator` bo'lganda,
    # `provider_key_index` o'sha provayderning qaysi kaliti (`settings.
    # GOOGLE_TRANSLATE_KEYS`/`AZURE_TRANSLATOR_KEYS`dagi tartib raqami)
    # ishlatilganini bildiradi — har bir kalit alohida oylik bepul belgi
    # hajmiga ega bo'lishi mumkin, shuning uchun alohida hisoblanadi.
    provider_key_index = Column(Integer)
    # Faqat `input_kind='photo'` uchun to'ldiriladi — rasmdan matnni qaysi
    # OCR provayder (ocrspace / google_vision) ajratganini bildiradi.
    # `provider` bilan aralashmasin: u har doim tarjima dvigateli.
    ocr_provider = Column(String(32))
    # `ocr_provider='ocrspace'` bo'lganda, `settings.OCRSPACE_KEYS`
    # ro'yxatidagi QAYSI kalit ishlatilganini bildiradi (0-based) — bir
    # nechta kalit navbat bilan ishlatilganda har birining oylik bepul
    # hajmini alohida hisoblash uchun kerak.
    ocr_key_index = Column(Integer)

    status = Column(String(20), default="success", server_default=sql_text("'success'"), nullable=False)
    error_code = Column(String(64))
    error_message = Column(Text)

    latency_ms = Column(Integer)
    cache_hit = Column(Boolean, default=False, nullable=False)

    created_at = _created_at()
    meta = _meta()

    __table_args__ = (
        # btree teskari yo'nalishda ham skanlanadi, shuning uchun DESC belgilash shart emas.
        Index("idx_translations_user_created", "user_id", "created_at"),
        # OCR provayder/kalit oylik bepul hajmini hisoblash uchun (`ocr.py`).
        Index(
            "idx_translations_ocr_monthly",
            "input_kind", "ocr_provider", "ocr_key_index", "created_at",
        ),
        # Tarjima provayder/kalit oylik bepul BELGI hajmini hisoblash uchun
        # (`translation.py`) — xuddi shu naqsh, OCR emas, tarjima uchun.
        Index(
            "idx_translations_provider_monthly",
            "provider", "provider_key_index", "created_at",
        ),
        Index("idx_translations_lang_pair", "source_lang_detected", "target_lang"),
        # BRIN — append-only vaqt ustuni uchun btree'dan minglab marta arzon.
        Index(
            "idx_translations_created_brin",
            "created_at",
            postgresql_using="brin",
        ),
        # Faqat muvaffaqiyatsizlarni indekslaymiz — 99% qatorlar indeksga tushmaydi.
        Index(
            "idx_translations_failures",
            "status",
            "created_at",
            postgresql_where=sql_text("status <> 'success'"),
        ),
        # Media bo'yicha qidirish/dedup uchun. Xuddi shu sabab bilan qisman:
        # tarjimalarning ~99% i oddiy matn, ularda bu ustun NULL.
        Index(
            "idx_translations_media_unique",
            "media_file_unique_id",
            postgresql_where=sql_text("media_file_unique_id IS NOT NULL"),
        ),
        CheckConstraint("chat_type IN " + str(CHAT_TYPES), name="ck_translations_chat_type"),
        CheckConstraint("input_kind IN " + str(INPUT_KINDS), name="ck_translations_input_kind"),
        CheckConstraint("status IN " + str(TRANSLATION_STATUSES), name="ck_translations_status"),
    )


# Sifat signallari. Musbat = yaxshi tarjima, manfiy = yomon.
SIGNAL_WEIGHTS = {
    "copied": 1,
    "tts_played": 1,
    "favorited": 2,
    "unfavorited": -1,
    "retranslated": -1,
    "lang_switched_after": -1,
    "reported": -2,
    "corrected": -2,
}
SIGNALS = tuple(SIGNAL_WEIGHTS)


class TranslationSignal(Base):
    """Implicit va explicit feedback — tarjima sifatini o'lchash uchun.

    Hozirgi tizimda bunday ma'lumot umuman yo'q, shuning uchun "qaysi tarjimalar
    yomon" degan savolga javob ham yo'q. Birinchi kundan yig'ilishi kerak —
    orqaga qaytarib to'plab bo'lmaydi.
    """

    __tablename__ = "translation_signals"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    translation_id = Column(
        BigInteger, ForeignKey("translations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True)

    signal = Column(String(32), nullable=False)
    # User o'zi taklif qilgan to'g'ri variant — oltin namuna.
    correction = Column(Text)

    created_at = _created_at()
    meta = _meta()

    __table_args__ = (
        Index("idx_signals_type_created", "signal", "created_at"),
        CheckConstraint("signal IN " + str(SIGNALS), name="ck_signals_signal"),
    )


TTS_STATUSES = ("success", "error", "timeout")


class TtsRequest(Base):
    __tablename__ = "tts_requests"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True)
    translation_id = Column(BigInteger, ForeignKey("translations.id", ondelete="SET NULL"))

    text = Column(Text, nullable=False)
    text_hash = Column(String(64), index=True)
    lang = Column(String(10), nullable=False)
    voice = Column(String(64))
    provider = Column(String(32), nullable=False)

    duration_ms = Column(Integer)
    file_size = Column(Integer)
    # Telegram fayl ID — bir xil matn qayta so'ralsa qayta generatsiya qilinmaydi.
    telegram_file_id = Column(String(255))

    status = Column(String(20), default="success", server_default=sql_text("'success'"), nullable=False)
    error_code = Column(String(64))
    latency_ms = Column(Integer)

    created_at = _created_at()
    meta = _meta()

    __table_args__ = (
        CheckConstraint("status IN " + str(TTS_STATUSES), name="ck_tts_status"),
    )


# ─────────────────────────────────────────────────────────────
#  Blok 3 — Harakatlar jurnali
# ─────────────────────────────────────────────────────────────

EVENT_SOURCES = ("bot", "admin", "system", "webhook")


class Event(Base):
    """Botdagi har bir harakat. Append-only.

    Bitta keng jadval + JSONB payload: yangi voqea turi qo'shish migratsiya
    talab qilmaydi. Tip xavfsizligi Python tarafda `EventType` bilan ta'minlanadi.

    Bo'laklash (partitioning) hozircha kerak emas — ~150k qator/oy. `events`
    20M dan oshganda oylik RANGE bo'laklashga o'tiladi; BRIN indeksi
    o'sha o'tishni oldindan tayyorlab qo'yadi.
    """

    __tablename__ = "events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    chat_id = Column(BigInteger)
    # 30 daqiqalik faollik oynasi — user yo'lini seans bo'yicha guruhlash uchun.
    session_id = Column(UUID(as_uuid=True))

    event_type = Column(String(64), nullable=False)
    source = Column(String(20), default="bot", server_default=sql_text("'bot'"), nullable=False)
    payload = Column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))

    created_at = _created_at()

    __table_args__ = (
        Index("idx_events_user_created", "user_id", "created_at"),
        Index("idx_events_type_created", "event_type", "created_at"),
        Index("idx_events_created_brin", "created_at", postgresql_using="brin"),
        Index("idx_events_payload", "payload", postgresql_using="gin"),
        CheckConstraint("source IN " + str(EVENT_SOURCES), name="ck_events_source"),
    )


class DailyUsage(Base):
    """Kunlik limit hisobi. `events` dan COUNT(*) qilish qimmat bo'lgani uchun alohida.

    Redis'da dublikati turadi (tezlik uchun), bu jadval — haqiqat manbai.
    """

    __tablename__ = "daily_usage"

    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    date = Column(Date, primary_key=True)

    translations_count = Column(Integer, default=0, server_default=sql_text("0"), nullable=False)
    tts_count = Column(Integer, default=0, server_default=sql_text("0"), nullable=False)
    images_count = Column(Integer, default=0, server_default=sql_text("0"), nullable=False)
    chars_count = Column(Integer, default=0, server_default=sql_text("0"), nullable=False)

    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (Index("idx_daily_usage_date", "date"),)


# ─────────────────────────────────────────────────────────────
#  Blok 4 — Boshqaruv
# ─────────────────────────────────────────────────────────────


class SystemSettings(Base):
    """Yagona qatorli (id=1) konfiguratsiya — admin panelidan sozlanadigan
    umumiy standart limitlar.

    `NULL` — `.env` dagi standart qiymat ishlatiladi (`bot/config/settings.py`).
    Boshqa qiymat qo'yilsa shu ustun turadi. `UserSettings.*_override` bilan
    aralashtirmaslik kerak: bu yerdagi qiymat HAMMA uchun standart, u yerdagi
    — bitta foydalanuvchiga alohida.
    """

    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True)

    daily_translation_limit = Column(Integer)
    daily_tts_limit = Column(Integer)
    daily_image_limit_free = Column(Integer)
    daily_image_limit_vip = Column(Integer)

    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Channel(Base):
    """Majburiy obuna kanallari."""

    __tablename__ = "channels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(BigInteger, unique=True, nullable=False)
    channel_username = Column(String(255), unique=True)
    channel_title = Column(String(255), nullable=False)
    button_text = Column(String(255), nullable=False)
    button_url = Column(String(512), nullable=False)

    is_active = Column(Boolean, default=True, server_default=sql_text("true"), nullable=False, index=True)
    priority = Column(Integer, default=0, server_default=sql_text("0"), nullable=False)
    added_by = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))

    created_at = _created_at()
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    meta = _meta()


BROADCAST_MODES = ("copy", "forward")
# `*_requested` — admin signalni bazaga yozdi, lekin ishlab turgan
# background vazifa hali buni ko'rmagan (u har ~2-3 soniyada tekshiradi).
# `paused` — vazifa signalni ko'rib, kursorini saqlab, o'zini toza
# to'xtatdi; qayta boshlash uchun YANGI background vazifa kerak bo'ladi
# (eskisi tugagan). Bot restart bo'lsa `running`/`pause_requested`/
# `cancel_requested` holatidagi qatorlar ishga tushishda `paused`ga
# o'tkaziladi — ularni "hali ketyapti" deb hisoblashning iloji yo'q,
# chunki ularni yuritayotgan vazifa jarayon bilan birga o'lgan.
BROADCAST_STATUSES = (
    "created",
    "running",
    "pause_requested",
    "paused",
    "cancel_requested",
    "cancelled",
    "completed",
    "failed",
)


class Broadcast(Base):
    __tablename__ = "broadcasts"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    created_by = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True)

    mode = Column(String(20), nullable=False)
    content_preview = Column(Text)
    status = Column(String(20), default="created", server_default=sql_text("'created'"), nullable=False, index=True)

    # Manba xabar — bazaga yoziladi, `Message` obyektiga emas, chunki
    # pauzadan keyingi YANGI background vazifa yoki bot restartidan keyingi
    # tiklash paytida asl `Message` allaqachon yo'q bo'ladi.
    src_chat_id = Column(BigInteger)
    src_message_id = Column(BigInteger)

    # Oxirgi qayta ishlangan `users.id` — davom ettirish shu yerdan
    # boshlanadi. `users.id` tabiiy o'suvchi kursor: tarqatish davomida
    # qo'shilgan yangi user doim bundan katta, ya'ni alohida sinxronlashsiz
    # ham qamrab olinadi.
    cursor_user_id = Column(BigInteger)
    # Faqat HAQIQATAN yuborilayotgan vaqt (navbat/pauza emas) — tezlik va
    # qolgan-vaqt hisobi shundan olinadi.
    active_seconds = Column(Integer, default=0, server_default=sql_text("0"), nullable=False)

    total_targets = Column(Integer, default=0, server_default=sql_text("0"), nullable=False)
    success_count = Column(Integer, default=0, server_default=sql_text("0"), nullable=False)
    failed_count = Column(Integer, default=0, server_default=sql_text("0"), nullable=False)

    # Fatal xato tafsiloti (masalan "xabar matni bo'sh") — nega to'xtaganini
    # tushuntiradi, faqat `status='failed'` bo'lganda to'ldiriladi.
    error = Column(Text)

    created_at = _created_at()
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    meta = _meta()

    deliveries = relationship(
        "BroadcastDelivery", back_populates="broadcast", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("mode IN " + str(BROADCAST_MODES), name="ck_broadcasts_mode"),
        CheckConstraint("status IN " + str(BROADCAST_STATUSES), name="ck_broadcasts_status"),
    )


DELIVERY_STATUSES = ("delivered", "failed", "blocked", "skipped")


class BroadcastDelivery(Base):
    __tablename__ = "broadcast_deliveries"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    broadcast_id = Column(
        BigInteger, ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True)

    status = Column(String(20), nullable=False)
    error = Column(Text)
    created_at = _created_at()

    broadcast = relationship("Broadcast", back_populates="deliveries")

    __table_args__ = (
        CheckConstraint("status IN " + str(DELIVERY_STATUSES), name="ck_deliveries_status"),
    )


class Chat(Base):
    """Bot qo'shilgan guruhlar."""

    __tablename__ = "chats"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, unique=True, nullable=False, index=True)
    title = Column(String(255))
    username = Column(String(255))
    type = Column(String(20), nullable=False)

    member_count = Column(Integer)
    is_active = Column(Boolean, default=True, server_default=sql_text("true"), nullable=False, index=True)
    added_by = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))

    # Guruhga xos sozlamalar — auto-tarjima yoqilganmi, qaysi tilga va h.k.
    source_lang = Column(String(10), default="auto", server_default=sql_text("'auto'"), nullable=False)
    target_lang = Column(String(10), default="uz", server_default=sql_text("'uz'"), nullable=False)

    created_at = _created_at()
    last_seen_at = _created_at()
    meta = _meta()


class SupportMessage(Base):
    """Adminga murojaat yozishmasi.

    Suhbat Telegram'ning "reply" mexanizmi orqali boradi: admin murojaat
    xabariga javob yozsa u foydalanuvchiga yetadi, foydalanuvchi javobga
    javob yozsa adminga qaytadi. Holat (FSM) saqlanmaydi — javob berilayotgan
    xabarning o'zi suhbatni aniqlaydi.

    Shuning uchun har bir yetkazilgan xabar uchun ikki uchning `message_id`
    si yoziladi: keyinchalik `reply_to_message.message_id` bo'yicha qaysi
    foydalanuvchi haqida gap ketayotganini topamiz.

    Bir murojaat bir necha adminga yuborilsa, har biriga alohida qator
    yoziladi — har bir admin chatida `message_id` boshqacha bo'ladi.
    """

    __tablename__ = "support_messages"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # `in` — foydalanuvchidan adminga, `out` — admindan foydalanuvchiga.
    direction = Column(String(3), nullable=False)
    text = Column(Text, nullable=False)

    admin_chat_id = Column(BigInteger, nullable=False)
    admin_message_id = Column(BigInteger, nullable=False)
    user_chat_id = Column(BigInteger, nullable=False)
    user_message_id = Column(BigInteger)

    # Media murojaat — `text` da faqat belgi (`[🖼 rasm]`) qoladi, faylning
    # o'zi `copy_message` bilan uzatiladi va bazaga tushmasdi. Endi fayl
    # identifikatorlari ham yoziladi (izoh: `bot/utils/media.py`).
    media_kind = Column(String(20))
    media_file_id = Column(Text)
    media_file_unique_id = Column(String(64))
    media_mime_type = Column(String(128))
    media_file_size = Column(BigInteger)
    media_file_name = Column(Text)

    created_at = _created_at(index=True)
    meta = _meta()

    user = relationship("User")

    # DIQQAT: `admin_chat_id`/`admin_message_id`/`user_chat_id`/
    # `user_message_id` faqat TARIX/audit uchun saqlanadi — 2026-08-27
    # qayta qurilgach suhbatni ANIQLASH uchun ENDI ishlatilmaydi (buni
    # `admin_reply_targets` pin mexanizmi qiladi). Shu sababli ilgari
    # shu ustunlar bo'yicha qidiruv uchun bo'lgan indekslar (migratsiya
    # 017da o'chirilgan) endi yo'q.
    __table_args__ = (
        CheckConstraint("direction IN ('in', 'out')", name="ck_support_direction"),
        # Qisman — murojaatlarning ko'pchiligi oddiy matn (`translations`
        # dagi bilan bir xil naqsh).
        Index(
            "idx_support_media_unique",
            "media_file_unique_id",
            postgresql_where=sql_text("media_file_unique_id IS NOT NULL"),
        ),
    )


class AdminReplyTarget(Base):
    """Admin "↩️ Javob yozish" tugmasini bosganda TANLAGAN suhbat.

    Nega kerak: `SupportMessage.admin_message_id` orqali topish
    `message.reply_to_message`ga tayanadi — Telegram esa buni ESKI
    xabarlar uchun (amalda bir necha soatdan keyin, hujjatlashtirilgan
    limitdan qat'i nazar) UZATMASLIGI mumkin, shunda javob oddiy
    tarjima sifatida ketib qolardi (2026-08-27 haqiqiy voqea). Shuning
    uchun har bir murojaat sarlavhasida tugma bor — bosilsa, reply
    ishlamasa ham, KEYINGI oddiy xabar shu foydalanuvchiga boradi.

    FSM EMAS — oddiy DB qatori, muddatsiz saqlanadi (foydalanilgach
    o'zi tozalanadi, vaqt bo'yicha emas). Har bir admin uchun bitta
    qator (`admin_chat_id` — PK): yangi tanlov eskisini almashtiradi.
    """

    __tablename__ = "admin_reply_targets"

    admin_chat_id = Column(BigInteger, primary_key=True)
    target_user_id = Column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    set_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    target_user = relationship("User")


class Donation(Base):
    """Telegram Stars orqali homiylik.

    Pul yozuvi — hech qachon o'chirilmaydi va o'zgartirilmaydi.
    `telegram_payment_charge_id` unikal: Telegram bir to'lov haqida bir necha
    marta xabar berishi mumkin, takroriy yozuv bo'lmasligi kerak. Shu id
    qaytarish (`refundStarPayment`) uchun ham kerak, shuning uchun majburiy.
    """

    __tablename__ = "donations"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True)

    # Stars butun son bo'lib keladi (XTR valyutasida kasr yo'q).
    stars = Column(Integer, nullable=False)
    currency = Column(String(10), default="XTR", server_default=sql_text("'XTR'"), nullable=False)

    telegram_payment_charge_id = Column(String(255), unique=True, nullable=False)
    provider_payment_charge_id = Column(String(255))
    invoice_payload = Column(String(255))

    status = Column(
        String(20), default="paid", server_default=sql_text("'paid'"), nullable=False, index=True
    )
    refunded_at = Column(DateTime(timezone=True))

    created_at = _created_at(index=True)
    meta = _meta()

    __table_args__ = (
        CheckConstraint(
            "status IN ('paid', 'refunded')", name="ck_donations_status"
        ),
        CheckConstraint("stars > 0", name="ck_donations_stars_positive"),
    )


class AdminAction(Base):
    """Admin nima qilgani — audit izi."""

    __tablename__ = "admin_actions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    admin_id = Column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"), index=True)

    action = Column(String(64), nullable=False)
    target_type = Column(String(32))
    target_id = Column(String(64))
    payload = Column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))

    created_at = _created_at(index=True)
