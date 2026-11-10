#!/usr/bin/env python3
"""O'tgan tarqatishlardagi doimiy xatolarga qarab userlarni faolsizlantiradi.

Nima uchun kerak:
    Avtomatik faolsizlantirish `is_permanently_unreachable()` bilan endi
    tarqatish paytida ishlaydi. Lekin bundan oldingi tarqatishlarda "chat not
    found" va shunga o'xshash xato olgan userlar `active` bo'lib qolgan —
    ular har bir keyingi tarqatishda qaytadan urinilib, har safar xato beradi.

    Bu skript o'sha eski yozuvlarni bir marta tozalaydi.

Tasniflash mantiqi takrorlanmaydi: `bot/services/broadcast.py` dagi bitta
funksiya ishlatiladi. SQL'da `LIKE` bilan qayta yozish ikki manba yaratardi
va vaqt o'tib ular bir-biridan uzoqlashardi.

Ishga tushirish:
    python scripts/mark_unreachable.py --dry-run   # faqat hisoblash
    python scripts/mark_unreachable.py             # belgilash
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update

from bot.database.models import BroadcastDelivery, User
from bot.database.session import AsyncSessionLocal, engine
from bot.services.broadcast import is_permanently_unreachable

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
)
log = logging.getLogger("unreachable")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Yetib bo'lmaydigan userlarni belgilash")
    parser.add_argument("--dry-run", action="store_true", help="o'zgartirmasdan hisoblash")
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        # Faqat hozir `active` bo'lganlar muhim: `blocked_bot` allaqachon
        # ro'yxatdan chiqarilgan, `deleted` esa allaqachon belgilangan.
        rows = await session.execute(
            select(BroadcastDelivery.user_id, BroadcastDelivery.error)
            .join(User, User.id == BroadcastDelivery.user_id)
            .where(
                BroadcastDelivery.status == "failed",
                User.status == "active",
            )
        )

        # Bitta user bir necha tarqatishda xato olgan bo'lishi mumkin —
        # birortasi doimiy bo'lsa yetarli.
        to_mark: dict[int, str] = {}
        reasons: Counter[str] = Counter()

        for user_id, error in rows.all():
            if user_id is None or not is_permanently_unreachable(error):
                continue
            to_mark.setdefault(user_id, error or "")
            reasons[_short(error)] += 1

        log.info("Belgilanadigan user: %s", f"{len(to_mark):,}".replace(",", " "))
        for reason, count in reasons.most_common():
            log.info("  %s — %s", reason, f"{count:,}".replace(",", " "))

        if not to_mark:
            log.info("Belgilanadigan yozuv yo'q.")
            await engine.dispose()
            return

        if args.dry_run:
            log.info("--dry-run: hech narsa o'zgartirilmadi.")
            await engine.dispose()
            return

        # Bo'laklab yangilaymiz: 4k id'ni bitta `IN` ga tiqish so'rovni
        # cheksiz uzaytiradi.
        ids = list(to_mark)
        chunk = 500
        for offset in range(0, len(ids), chunk):
            await session.execute(
                update(User)
                .where(User.id.in_(ids[offset : offset + chunk]))
                .values(status="deleted")
            )
            await session.commit()

        log.info("✅ %s user `deleted` deb belgilandi", f"{len(ids):,}".replace(",", " "))

        counts = await session.execute(
            select(User.status, __import__("sqlalchemy").func.count(User.id)).group_by(
                User.status
            )
        )
        for status, count in counts.all():
            log.info("  %s: %s", status, f"{count:,}".replace(",", " "))

    await engine.dispose()


def _short(error: str | None) -> str:
    """Xato matnini guruhlash uchun qisqartiradi."""
    if not error:
        return "(sabab yo'q)"
    lowered = error.lower()
    for marker in ("chat not found", "user_bot_to_bot_disabled", "peer_id_invalid"):
        if marker in lowered:
            return marker
    return error[:40]


if __name__ == "__main__":
    asyncio.run(main())
