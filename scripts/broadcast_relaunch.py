#!/usr/bin/env python3
"""Qayta ishga tushish xabarini barcha foydalanuvchilarga tarqatadi.

Nega alohida skript, admin panelidagi tarqatish emas:
    Admin panelidagi tarqatish adminning bitta xabarini ko'chiradi, ya'ni
    hamma bir xil tilda oladi. Bu yerda esa har bir foydalanuvchi o'z tilida
    (`user_settings.interface_lang`) va o'z tilidagi asosiy menyu tugmalari
    bilan oladi.

Xususiyatlar:
  - **Uzilsa davom etadi.** Har bir yuborilgan xabar `broadcast_deliveries` ga
    yoziladi. `--resume <id>` bilan qayta ishga tushirilganda allaqachon
    yuborilganlar o'tkazib yuboriladi. 37k xabar ~30 daqiqa oladi, shu vaqt
    ichida uzilish ehtimoli real.
  - **Bloklaganlarni belgilaydi.** `TelegramForbiddenError` — foydalanuvchi
    botni bloklagan; `users.status` `blocked_bot` ga o'tadi va keyingi
    tarqatishlarda behuda urinish bo'lmaydi.
  - **Telegram limitini hurmat qiladi.** `TelegramRetryAfter` da kutadi.

Ishga tushirish:
    python scripts/broadcast_relaunch.py --dry-run          # faqat hisoblash
    python scripts/broadcast_relaunch.py --limit 20         # sinov uchun 20 ta
    python scripts/broadcast_relaunch.py --only-me          # faqat adminlarga
    python scripts/broadcast_relaunch.py                    # hammaga
    python scripts/broadcast_relaunch.py --resume 12        # uzilgandan keyin
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from sqlalchemy import func, select, update

from bot import locales
from bot.config.settings import settings
from bot.database.models import BroadcastDelivery, User, UserSettings
from bot.database.repositories.broadcast_repository import BroadcastRepository
from bot.database.session import AsyncSessionLocal, engine
from bot.keyboards.user import main_menu

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
)
log = logging.getLogger("relaunch")

# Telegram bulk yuborishda ~30 xabar/soniyaga ruxsat beradi. 20 ni olamiz:
# limitga tegib RetryAfter yeb, umumiy vaqtni uzaytirgandan ko'ra barqaror
# tezlik afzal.
BATCH = 20
BATCH_PAUSE = 1.0
COMMIT_EVERY = 100


async def load_targets(session, *, limit: int | None, only_admins: bool):
    """`(user_id, telegram_id, interface_lang)` ro'yxati.

    Faqat `active` — botni bloklaganlarga yuborish Telegram limitini behuda
    sarflaydi va baribir yetib bormaydi.
    """
    query = (
        select(User.id, User.telegram_id, UserSettings.interface_lang)
        .join(UserSettings, UserSettings.user_id == User.id)
        .where(User.status == "active")
        .order_by(User.id)
    )
    if only_admins:
        query = query.where(User.telegram_id.in_(settings.ADMIN_USER_IDS))
    if limit:
        query = query.limit(limit)
    return [(r[0], r[1], r[2]) for r in (await session.execute(query)).all()]


async def already_sent(session, broadcast_id: int) -> set[int]:
    rows = await session.execute(
        select(BroadcastDelivery.user_id).where(
            BroadcastDelivery.broadcast_id == broadcast_id,
            BroadcastDelivery.status == "delivered",
        )
    )
    return {r[0] for r in rows.all()}


async def main() -> None:
    parser = argparse.ArgumentParser(description="Qayta ishga tushish xabarini tarqatish")
    parser.add_argument("--dry-run", action="store_true", help="yubormasdan hisoblash")
    parser.add_argument("--limit", type=int, default=None, help="nechta userga (sinov)")
    parser.add_argument("--only-me", action="store_true", help="faqat adminlarga")
    parser.add_argument("--resume", type=int, default=None, help="mavjud broadcast id")
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        targets = await load_targets(
            session, limit=args.limit, only_admins=args.only_me
        )

        by_lang = Counter(lang for _, _, lang in targets)
        log.info("Nishon: %s user", f"{len(targets):,}".replace(",", " "))
        for lang, count in by_lang.most_common():
            log.info("  %s: %s", lang, f"{count:,}".replace(",", " "))

        if args.dry_run:
            total_active = (
                await session.execute(
                    select(func.count(User.id)).where(User.status == "active")
                )
            ).scalar_one()
            log.info("Jami aktiv user: %s", f"{total_active:,}".replace(",", " "))
            log.info(
                "Taxminiy vaqt: ~%.0f daqiqa",
                len(targets) / BATCH * BATCH_PAUSE / 60,
            )
            log.info("--dry-run: hech narsa yuborilmadi.")
            await engine.dispose()
            return

        repo = BroadcastRepository(session)

        if args.resume:
            broadcast_id = args.resume
            done = await already_sent(session, broadcast_id)
            log.info(
                "Davom ettirish: broadcast=%s, allaqachon yuborilgan=%s",
                broadcast_id,
                f"{len(done):,}".replace(",", " "),
            )
            targets = [t for t in targets if t[0] not in done]
            log.info("Qolgan: %s", f"{len(targets):,}".replace(",", " "))
        else:
            broadcast = await repo.create_broadcast(
                created_by=None,
                mode="copy",
                content_preview="[relaunch] tilga qarab qayta ishga tushish xabari",
                total_targets=len(targets),
            )
            await session.commit()
            broadcast_id = broadcast.id
            log.info("Yangi broadcast id=%s", broadcast_id)
            log.info("Uzilsa: --resume %s bilan davom ettiring", broadcast_id)

        bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

        # Har bir til uchun matn va klaviatura bir marta tayyorlanadi —
        # 37k marta qayta qurish behuda.
        prepared = {
            code: (locales.get(code).RELAUNCH, main_menu(locales.get(code)))
            for code in locales.SUPPORTED
        }

        success = 0
        failed = 0
        blocked = 0

        try:
            for index, (user_id, telegram_id, lang) in enumerate(targets, start=1):
                text, markup = prepared.get(lang, prepared[locales.DEFAULT])

                delivered = False
                error_text: str | None = None

                for attempt in range(3):
                    try:
                        await bot.send_message(
                            telegram_id, text, reply_markup=markup
                        )
                        delivered = True
                        break
                    except TelegramRetryAfter as exc:
                        wait = max(int(getattr(exc, "retry_after", 1)), 1)
                        log.warning("Limit: %s soniya kutamiz", wait)
                        await asyncio.sleep(wait)
                    except TelegramForbiddenError:
                        # Botni bloklagan yoki akkauntni o'chirgan.
                        error_text = "forbidden"
                        await session.execute(
                            update(User)
                            .where(User.id == user_id)
                            .values(status="blocked_bot", blocked_at=func.now())
                        )
                        blocked += 1
                        break
                    except TelegramBadRequest as exc:
                        # Chat topilmadi va shunga o'xshash qaytarib
                        # bo'lmaydigan xatolar — qayta urinish foydasiz.
                        error_text = str(exc)[:200]
                        break
                    except TelegramAPIError as exc:
                        error_text = str(exc)[:200]
                        await asyncio.sleep(1 + attempt)

                if delivered:
                    success += 1
                    await repo.add_delivery(broadcast_id, user_id, "delivered")
                else:
                    failed += 1
                    await repo.add_delivery(broadcast_id, user_id, "failed", error_text)

                if index % COMMIT_EVERY == 0:
                    await session.commit()
                    log.info(
                        "%s/%s — yuborildi %s, xato %s, bloklagan %s",
                        index,
                        len(targets),
                        success,
                        failed,
                        blocked,
                    )

                if index % BATCH == 0:
                    await asyncio.sleep(BATCH_PAUSE)

            await session.commit()
            await repo.finish_broadcast(broadcast_id, success, failed)
            await session.commit()
            log.info(
                "✅ Tugadi: yuborildi %s, xato %s, bloklagan %s",
                f"{success:,}".replace(",", " "),
                failed,
                blocked,
            )
        except (KeyboardInterrupt, asyncio.CancelledError):
            await session.commit()
            log.warning(
                "To'xtatildi. Davom ettirish: --resume %s (yuborilgan: %s)",
                broadcast_id,
                success,
            )
        finally:
            await bot.session.close()
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
