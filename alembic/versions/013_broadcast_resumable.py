"""Tarqatishni pauza/davom ettirish va admin band bo'lib qolmasligi.

Nima va nega:
  - `broadcasts.src_chat_id` / `src_message_id` — manba xabar qayerdan
    olinganini bazaga yozadi. Ilgari bu faqat aiogram `Message` obyektida
    (xotirada) bor edi — pauza qilingandan keyin YANGI background vazifa
    boshlanganda yoki bot restart bo'lganda manba xabar qayerda ekanini
    bilishning boshqa yo'li yo'q edi.
  - `broadcasts.cursor_user_id` — oxirgi qayta ishlangan `users.id`.
    `users.id` — avtomatik ortib boruvchi PK, ya'ni tabiiy ravishda
    "hech qachon orqaga qaytmaydigan" kursor: tarqatish davomida
    ro'yxatdan o'tgan YANGI foydalanuvchi doim kursordan OLDINDA bo'ladi,
    demak tugallanmagan tarqatish ularga ham yetadi (alohida sinxronlash
    kerak emas, chunki manba — botning o'z `users` jadvali).
  - `broadcasts.active_seconds` — tarqatish HAQIQATAN yuborayotgan holatda
    turgan vaqt (navbatda/pauzada emas). Tezlik va qolgan-vaqt hisobi
    shundan olinadi — aks holda soatlab pauza qilingan tarqatish
    "juda sekin" ko'rinardi.
  - `status` ro'yxatiga `pause_requested`/`paused` qo'shildi —
    `cancel_requested`/`cancelled` bilan bir xil naqsh: admin signalni
    bazaga yozadi, ishlab turgan background vazifa buni keyingi
    tekshiruvida ko'radi va o'zini toza to'xtatadi.

Bularning barchasi bitta narsa uchun: admin "📤 Yuborish" bosgach ORQAGA
QAYTIB boshqa ish bilan shug'ullana olsin (hozir esa `run_broadcast()`
adminning o'z chatini tarqatish tugaguncha band qilib turadi — aiogram har
bir yangilanishni FSM qulfi ostida ishlaydi), va tarqatish bot qayta
ishga tushsa ham yo'qolmasin.
"""

from alembic import op
import sqlalchemy as sa


revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None

OLD_STATUSES = (
    "created", "running", "cancel_requested", "cancelled", "completed", "failed",
)
NEW_STATUSES = (
    "created", "running", "pause_requested", "paused",
    "cancel_requested", "cancelled", "completed", "failed",
)


def upgrade() -> None:
    op.add_column("broadcasts", sa.Column("src_chat_id", sa.BigInteger()))
    op.add_column("broadcasts", sa.Column("src_message_id", sa.BigInteger()))
    op.add_column("broadcasts", sa.Column("cursor_user_id", sa.BigInteger()))
    op.add_column(
        "broadcasts",
        sa.Column(
            "active_seconds", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    # Fatal xato tafsiloti (masalan "xabar matni bo'sh") — admin uchun,
    # tarqatish nega to'xtaganini tushuntiradi.
    op.add_column("broadcasts", sa.Column("error", sa.Text()))

    op.drop_constraint("ck_broadcasts_status", "broadcasts", type_="check")
    op.create_check_constraint(
        "ck_broadcasts_status", "broadcasts", "status IN " + str(NEW_STATUSES)
    )


def downgrade() -> None:
    # Yangi holatlardagi qatorlar bo'lsa eski CHECK'ga to'g'ri kelmaydi —
    # downgrade shu tarqatishlarni "failed" ga o'tkazadi (nol yo'qotish
    # yo'q, faqat holat yorlig'i).
    op.execute(
        "UPDATE broadcasts SET status = 'failed' "
        "WHERE status IN ('pause_requested', 'paused')"
    )
    op.drop_constraint("ck_broadcasts_status", "broadcasts", type_="check")
    op.create_check_constraint(
        "ck_broadcasts_status", "broadcasts", "status IN " + str(OLD_STATUSES)
    )

    op.drop_column("broadcasts", "error")
    op.drop_column("broadcasts", "active_seconds")
    op.drop_column("broadcasts", "cursor_user_id")
    op.drop_column("broadcasts", "src_message_id")
    op.drop_column("broadcasts", "src_chat_id")
