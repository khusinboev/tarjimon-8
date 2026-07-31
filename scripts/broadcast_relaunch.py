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

  - **Tezligi cheklangan.** Semafor tarmoq kutishini yashiradi, `RateLimiter`
    esa Telegram'ga ketadigan oqimni tekis ushlaydi (batafsil:
    `bot/services/broadcast.py`). Ketma-ket yuborishda tezlik ~5/sek edi.

Ishga tushirish:
    python scripts/broadcast_relaunch.py --dry-run          # faqat hisoblash
    python scripts/broadcast_relaunch.py --limit 100        # birinchi 100 ta
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
from bot.database.repositories.user_repository import UserRepository
from bot.database.session import AsyncSessionLocal, engine
from bot.keyboards.user import main_menu
from bot.services.broadcast import (
    DEFAULT_CONCURRENCY,
    DEFAULT_RATE_PER_SEC,
    RateLimiter,
    is_permanently_unreachable,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout
)
log = logging.getLogger("relaunch")

# Bir marta `gather` qilinadigan miqdor. Batch oxirida bazaga yozamiz va
# jarayonni logga chiqaramiz, ya'ni uzilganda ko'pi bilan shu qadar yozuv
# qayta yuboriladi.
BATCH = 50
COMMIT_EVERY = 500


async def load_targets(session, *, limit: int | None):
    """`(user_id, telegram_id, interface_lang)` ro'yxati.

    `blocked_bot` ham kiradi: bloklash qaytariladigan holat va odam botni
    blokdan chiqargan bo'lishi mumkin. Telegram bu haqda xabar bermaydi —
    bilishning yagona yo'li yuborib ko'rish. Yetib borsa `active` ga
    qaytariladi.

    `deleted` kirmaydi: chat umuman mavjud emas, urinish behuda.
    """
    query = (
        select(User.id, User.telegram_id, UserSettings.interface_lang)
        .join(UserSettings, UserSettings.user_id == User.id)
        .where(User.status.in_(UserRepository.BROADCAST_STATUSES))
        .order_by(User.id)
    )
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
    parser.add_argument("--limit", type=int, default=None, help="birinchi N userga")
    parser.add_argument("--resume", type=int, default=None, help="mavjud broadcast id")
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        targets = await load_targets(session, limit=args.limit)

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
                len(targets) / DEFAULT_RATE_PER_SEC / 60,
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

        limiter = RateLimiter(DEFAULT_RATE_PER_SEC)
        semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY)

        success = 0
        failed = 0
        blocked = 0
        unreachable = 0
        recovered = 0
        failures: list[tuple[int, str]] = []

        async def deliver(telegram_id: int, lang: str) -> tuple[bool, str | None]:
            """Telegram'ga yuboradi. Bazaga tegmaydi — chaqiruvchi yozadi.

            `AsyncSession` parallel ishlatishga xavfsiz emas, shuning uchun
            korutina faqat tarmoq ishini bajaradi.
            """
            text, markup = prepared.get(lang, prepared[locales.DEFAULT])

            async with semaphore:
                error_text: str | None = None

                for attempt in range(3):
                    await limiter.wait()
                    try:
                        await bot.send_message(telegram_id, text, reply_markup=markup)
                        return True, None
                    except TelegramRetryAfter as exc:
                        wait = max(int(getattr(exc, "retry_after", 1)), 1)
                        log.warning("Limit: %s soniya kutamiz", wait)
                        # Limitga bitta so'rov tegsa qolgani ham tegadi.
                        limiter.pause(wait + 0.5)
                        await asyncio.sleep(wait)
                    except TelegramForbiddenError:
                        # Botni bloklagan yoki akkauntni o'chirgan.
                        return False, "forbidden"
                    except TelegramBadRequest as exc:
                        # Chat topilmadi va shunga o'xshash qaytarib
                        # bo'lmaydigan xatolar — qayta urinish foydasiz.
                        return False, str(exc)[:200]
                    except TelegramAPIError as exc:
                        error_text = str(exc)[:200]
                        await asyncio.sleep(1 + attempt)

                return False, error_text or "noma'lum"

        try:
            for offset in range(0, len(targets), BATCH):
                batch = targets[offset : offset + BATCH]
                results = await asyncio.gather(
                    *(deliver(tg_id, lang) for _, tg_id, lang in batch),
                    return_exceptions=True,
                )
                reached: list[int] = []

                for (user_id, telegram_id, _lang), result in zip(batch, results):
                    if isinstance(result, BaseException):
                        delivered, error_text = False, str(result)[:200]
                    else:
                        delivered, error_text = result

                    if delivered:
                        success += 1
                        reached.append(user_id)
                        await repo.add_delivery(broadcast_id, user_id, "delivered")
                        continue

                    failed += 1
                    failures.append((telegram_id, error_text or "noma'lum"))
                    await repo.add_delivery(broadcast_id, user_id, "failed", error_text)

                    if error_text == "forbidden":
                        # Bloklagan — qaytishi mumkin, `/start` da tiklanadi.
                        await session.execute(
                            update(User)
                            .where(User.id == user_id)
                            .values(status="blocked_bot", blocked_at=func.now())
                        )
                        blocked += 1
                    elif is_permanently_unreachable(error_text):
                        # Chat umuman yo'q — keyingi tarqatishlarga tushmasin.
                        await session.execute(
                            update(User).where(User.id == user_id).values(status="deleted")
                        )
                        unreachable += 1

                # Bloklagan deb belgilangan odamga xabar yetib borgan bo'lsa —
                # u blokdan chiqargan. Bilishning yagona yo'li shu.
                recovered += await UserRepository(session).mark_many_unblocked(reached)

                await session.commit()

                processed = success + failed
                if processed % COMMIT_EVERY < BATCH:
                    log.info(
                        "%s/%s — yuborildi %s, xato %s, bloklagan %s",
                        processed,
                        len(targets),
                        success,
                        failed,
                        blocked,
                    )

            await session.commit()
            await repo.finish_broadcast(broadcast_id, success, failed)
            await session.commit()
            log.info(
                "✅ Tugadi: yuborildi %s, xato %s, bloklagan %s, "
                "yetib bo'lmaydi %s, qaytgan %s",
                f"{success:,}".replace(",", " "),
                failed,
                blocked,
                unreachable,
                recovered,
            )

            # Yetmagan foydalanuvchilar ro'yxati — keyin tekshirish uchun.
            if failures:
                path = Path(f"/home/tarjimon8/xato_{broadcast_id}.txt")
                try:
                    path.write_text(
                        "\n".join(f"{tg_id}\t{err}" for tg_id, err in failures),
                        encoding="utf-8",
                    )
                    log.info("Xato ro'yxati: %s (%s yozuv)", path, len(failures))
                except OSError as exc:
                    log.warning("Xato ro'yxatini yozib bo'lmadi: %s", exc)
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
