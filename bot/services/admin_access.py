"""Kim admin — YAGONA manba.

Ilgari loyihada ikki xil "admin" tushunchasi bir-biridan bexabar holda
yashardi:

  1. Admin PANELI (`handlers/admin/panel.py`) va murojaatga javob
     (`handlers/user/support.py`) — FAQAT `.env`dagi `ADMIN_USER_IDS`.
  2. Majburiy obuna/ban chetlab o'tishi (`middlewares/subscription.py`) —
     `.env` YOKI DB'dagi `User.role in ("admin", "owner")`.

Ikkalasi ham ATAYLAB shunday (xatti-harakat o'zgarmagan), lekin endi bitta
funksiya va ANIQ bayroq orqali ifodalanadi — qaysi joy qaysi tushunchani
ishlatayotgani kodning o'zida ko'rinib turadi.

Ishonch chegarasi:
  * `include_db_role=False` (standart) — faqat `.env`. Panel kirishi,
    tarqatish, foydalanuvchini bloklash, murojaatga javob va `/report`
    shunga tayanadi. DB qatori (kimdir SQL orqali o'zgartirishi mumkin)
    bu yerda HECH QACHON imtiyoz bermaydi — serverga deploy kirishi bor
    odamgina `.env`ni tahrirlay oladi.
  * `include_db_role=True` — `.env` YOKI DB roli. Faqat "yumshoq"
    imtiyozlar uchun: majburiy obunadan va ban'dan o'tib ketish
    (o'zini/boshqa adminni tasodifan bloklab qo'yishdan qutulish uchun).
    Panelga kirish BERMAYDI.

Yangi joyda admin tekshiruvi kerak bo'lsa — shu moduldan oling va
bayroqni ongli tanlang. Shubha bo'lsa — standart (`.env` faqat).

Eslatma: `services/admin_service.py` va `services/quota.py` hali o'z
tekshiruviga ega (rol bo'yicha cheksiz kvota va h.k.) — ular bu
o'zgarishga kirmagan, keyingi bosqichda shu yerga ko'chirilishi mumkin.
"""

from __future__ import annotations

from typing import Any, Optional

from bot.config.settings import settings

# DB'dagi `users.role` qiymatlari, ular `include_db_role=True` bo'lganda
# admin hisoblanadi (`models.USER_ROLES` bilan mos).
DB_ADMIN_ROLES = frozenset({"admin", "owner"})


def is_env_admin(telegram_id: Optional[int]) -> bool:
    """Faqat `.env` (`ADMIN_USER_IDS`). `None` — hech qachon admin emas."""
    if telegram_id is None:
        return False
    return telegram_id in settings.ADMIN_USER_IDS


def has_db_admin_role(user: Any) -> bool:
    """DB qatoridagi rol (`User.role`). `user` yo'q bo'lsa — `False`."""
    return getattr(user, "role", None) in DB_ADMIN_ROLES


def is_admin(
    telegram_id: Optional[int],
    user: Any = None,
    *,
    include_db_role: bool = False,
) -> bool:
    """Yagona admin tekshiruvi.

    `telegram_id` — Telegram'dagi foydalanuvchi ID (`from_user.id`).
    `user` — DB'dagi `User` (faqat `include_db_role=True` bo'lganda
    ishlatiladi; aks holda e'tiborga olinmaydi — imtiyoz oshib ketmasin).
    """
    if is_env_admin(telegram_id):
        return True
    if include_db_role:
        return has_db_admin_role(user)
    return False
