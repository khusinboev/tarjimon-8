"""Bir vaqtning o'zida faqat bitta faol tarqatish — DB darajasida kafolat.

Ilgari faqat kod darajasida tekshirilardi (`get_active()` bilan "faol
tarqatish bormi" so'raladi, bo'lmasa yangisi yaratiladi) — ikki so'rov
orasida (masalan ikki admin deyarli bir vaqtda "Boshlash"ni bossa) RACE
CONDITION bor edi: ikkalasi ham "faol tarqatish yo'q" deb ko'rib, ikkalasi
ham yangi tarqatish yaratardi — natijada hamma foydalanuvchiga xabar 2
marta ketardi.

Bu yerda "faqat bitta qator active statusda bo'lishi mumkin" qat'iy DB
cheklovi qo'shiladi — Postgres'da doimiy ifodali (`(true)`) PARTIAL UNIQUE
INDEX orqali: filtrga mos qatorlar orasida bir xil `(true)` qiymati faqat
BITTA marta uchrashi mumkin. Ikkinchi INSERT/UPDATE `IntegrityError`
ko'taradi — kod tomonida (`broadcast_repository.py`) ushlanadi.
"""

from alembic import op


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

_ACTIVE_STATUSES = ("running", "pause_requested", "paused", "cancel_requested")


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX ux_broadcasts_single_active ON broadcasts ((true)) "
        f"WHERE status IN {_ACTIVE_STATUSES}"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_broadcasts_single_active")
