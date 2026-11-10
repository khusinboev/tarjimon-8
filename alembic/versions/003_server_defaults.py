"""NOT NULL ustunlarga baza darajasidagi DEFAULT qo'shish

Revision ID: 003
Revises: 002
Create Date: 2026-07-30 00:45:00.000000

Nima uchun kerak:
    SQLAlchemy'dagi `default=False` — bu faqat Python tomonidagi qiymat, ORM
    orqali INSERT qilinganda ishlaydi. Bazada DEFAULT yaratmaydi. Natijada xom
    SQL bilan yozilganda (migratsiya skripti, `psql`, `COPY`, tashqi vositalar)
    ustun tashlab ketilsa, NotNullViolation chiqadi.

Qoida:
    Bazadagi DEFAULT modeldagi Python `default=` bilan **aynan bir xil**
    bo'ladi. Shunda bitta haqiqat manbai bo'ladi va `alembic autogenerate`
    keyinchalik farq ko'rib, DEFAULT'larni o'chirishga urinmaydi.

    Modelda `default=` bo'lmagan ustunlar (`translations.provider`,
    `tts_requests.provider`, `broadcast_deliveries.status`,
    `users.telegram_id`, `translations.source_text`, `events.event_type`)
    ataylab DEFAULT'siz qoldirilgan — ular har doim aniq berilishi kerak,
    va berilmasa xato baland ovozda chiqishi kerak.
"""
from alembic import op
import sqlalchemy as sa


revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


# (jadval, ustun, SQL default)
DEFAULTS: list[tuple[str, str, str]] = [
    # ── users ─────────────────────────────────────────────────
    ("users", "is_premium", "false"),
    ("users", "status", "'active'"),
    ("users", "role", "'user'"),
    # ── user_settings ─────────────────────────────────────────
    ("user_settings", "source_lang", "'auto'"),
    ("user_settings", "target_lang", "'uz'"),
    ("user_settings", "interface_lang", "'uz'"),
    ("user_settings", "tts_enabled", "true"),
    ("user_settings", "tts_auto", "false"),
    ("user_settings", "save_history", "true"),
    ("user_settings", "allow_training", "true"),
    # ── languages ─────────────────────────────────────────────
    ("languages", "supports_tts", "false"),
    ("languages", "supports_detection", "true"),
    ("languages", "is_active", "true"),
    ("languages", "sort_order", "100"),
    # ── translations ──────────────────────────────────────────
    ("translations", "chat_type", "'private'"),
    ("translations", "input_kind", "'text'"),
    ("translations", "source_lang_requested", "'auto'"),
    ("translations", "source_chars", "0"),
    ("translations", "target_chars", "0"),
    ("translations", "status", "'success'"),
    ("translations", "cache_hit", "false"),
    # ── events ────────────────────────────────────────────────
    ("events", "source", "'bot'"),
    # ── daily_usage ───────────────────────────────────────────
    ("daily_usage", "translations_count", "0"),
    ("daily_usage", "tts_count", "0"),
    ("daily_usage", "chars_count", "0"),
    # ── tts_requests ──────────────────────────────────────────
    ("tts_requests", "status", "'success'"),
    # ── broadcasts ────────────────────────────────────────────
    ("broadcasts", "status", "'created'"),
    ("broadcasts", "total_targets", "0"),
    ("broadcasts", "success_count", "0"),
    ("broadcasts", "failed_count", "0"),
    # ── channels ──────────────────────────────────────────────
    ("channels", "is_active", "true"),
    ("channels", "priority", "0"),
    # ── chats ─────────────────────────────────────────────────
    ("chats", "is_active", "true"),
    ("chats", "source_lang", "'auto'"),
    ("chats", "target_lang", "'uz'"),
]


def upgrade() -> None:
    for table, column, default in DEFAULTS:
        op.alter_column(
            table, column, server_default=sa.text(default),
        )


def downgrade() -> None:
    for table, column, _ in DEFAULTS:
        op.alter_column(table, column, server_default=None)
