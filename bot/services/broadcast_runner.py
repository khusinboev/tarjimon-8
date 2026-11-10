"""Tarqatish yuborish quvuri — uzluksiz, davom ettiriladigan, adminni band qilmaydi.

manager-bot (github.com/khusinboev/manager-bot) dan o'rganib moslashtirilgan
arxitektura, lekin BITTA jarayon ichida (alohida worker xizmati emas —
sabab: tarjimon-8ning tarqatishlari ancha kichik, ~soatdan kam davom etadi,
ikkinchi systemd xizmati keragidan ortiq murakkablik bo'lardi).

Ishga tushirilgandan keyin `asyncio.create_task` bilan FONDA ishlaydi —
adminning o'zi darhol javob oladi va boshqa ishlar bilan shug'ullanishi
mumkin (aiogram har bir yangilanishni FSM qulfi ostida ishlaydi, ya'ni
`await run_broadcast(...)` to'g'ridan-to'g'ri chaqirilganda adminning O'ZI
tarqatish tugaguncha bandlashib qolardi — asosiy sabab shu).

## Kursor — "uzluksiz tugallangan prefiks" (watermark)

Bir vaqtda bir nechta yuborish parallel ketayotgani uchun ular TARTIBSIZ
tugaydi. Agar kursorni oxirgi TUGAGAN foydalanuvchiga qo'ysak, orada hali
tugamagan (lekin allaqachon boshlangan) foydalanuvchilar pauza/restart
paytida ABADIY tashlab ketiladi. Shuning uchun `_dispatch_order` (jo'natish
tartibida) va `_done_ids` orqali faqat UZLUKSIZ tugallangan boshidan
kursor siljiydi — o'rtada tugallanmagan bo'lsa kursor o'sha yerda to'xtaydi.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Awaitable, Callable, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from bot.database.repositories.broadcast_repository import BroadcastRepository
from bot.database.repositories.user_repository import UserRepository
from bot.database.session import AsyncSessionLocal
from bot.services.broadcast import DEFAULT_CONCURRENCY, DEFAULT_RATE_PER_SEC, RateLimiter
from bot.services.broadcast_errors import Classification, Kind, classify
from bot.services.events import utcnow

logger = logging.getLogger(__name__)

FETCH_BATCH = 200
SEND_ATTEMPTS = 2
# Har bir yuborishda checkpoint qilish DB yozuvini portlatardi — shuning
# uchun bir nechtadan keyin saqlanadi. DIQQAT: bu ataylab qabul qilingan
# muvozanat — cho'kish (crash) checkpoint oralig'ida sodir bo'lsa, so'nggi
# checkpointdan keyin YUBORILGAN (lekin hali yozilmagan) qabul qiluvchilar
# tiklashda QAYTA xabar olishi mumkin (bounded, kamdan-kam holat — "exactly
# once" emas, "at-least-once" kafolati). 20/5s (~1.3s'da 20 ta, 15/sek
# tezlikda) oldin edi — 10/3s'ga qisqartirildi, DB yukini sezilarli
# oshirmasdan oynani deyarli 2 barobar kamaytiradi.
CHECKPOINT_EVERY_RESULTS = 10
CHECKPOINT_INTERVAL_SECONDS = 3.0
CONTROL_CHECK_INTERVAL_SECONDS = 2.0
# `total_targets` faqat KARTADA ko'rsatish uchun (yangi qo'shilgan
# foydalanuvchilar ham qamrab olinsin deb) — har safar bufer to'lganda
# (37 ming foydalanuvchida ~190 marta) emas, shu oraliqda qayta hisoblanadi.
TOTAL_REFRESH_INTERVAL_SECONDS = 30.0

ProgressNotify = Callable[[int, bool], Awaitable[None]]

# Jarayon ichida: broadcast_id -> vazifa. Bitta tarqatish ikki marta
# ishga tushmasligini kafolatlaydi (masalan admin "Yangilash"ni tez-tez
# bossa ham).
_active_tasks: dict[int, asyncio.Task] = {}


def is_running(broadcast_id: int) -> bool:
    task = _active_tasks.get(broadcast_id)
    return task is not None and not task.done()


def _unregister(broadcast_id: int, task: Optional[asyncio.Task]) -> None:
    """Faqat AYNAN shu `task` hali ro'yxatda bo'lsa olib tashlaydi.

    `_run()` pauza/bekor qilinganda DB holatini yozgach o'zini DARHOL
    (hali to'liq qaytmasdan) ro'yxatdan chiqaradi — aks holda "davom
    ettirish" xuddi shu daqiqada bosilsa, `is_running()` hali ESKI
    vazifani "ishlamoqda" deb ko'rib, YANGI vazifa boshlanmay, tarqatish
    "running" holatida ABADIY osilib qolishi mumkin edi (hech kim uni
    qayta ishga tushirmaguncha, faqat bot restart tiklaydi). Lekin shu
    vaqt oralig'ida yangi vazifa allaqachon ro'yxatga yozilib ulgurgan
    bo'lishi mumkin — shuning uchun identifikatorga emas, AYNAN shu
    `task` obyektiga tekshiramiz, aks holda eski vazifaning yakuniy
    tozalashi yangi vazifaning yozuvini bekor qilib qo'yardi.
    """
    if task is not None and _active_tasks.get(broadcast_id) is task:
        del _active_tasks[broadcast_id]


def start(bot: Bot, broadcast_id: int, progress_notify: Optional[ProgressNotify] = None) -> None:
    """Fon vazifasini ishga tushiradi (agar allaqachon ishlamayotgan bo'lsa)."""
    if is_running(broadcast_id):
        return
    task = asyncio.create_task(_guarded_run(bot, broadcast_id, progress_notify))
    _active_tasks[broadcast_id] = task


async def _guarded_run(
    bot: Bot, broadcast_id: int, progress_notify: Optional[ProgressNotify]
) -> None:
    try:
        await _run(bot, broadcast_id, progress_notify)
    except Exception:
        logger.exception("Tarqatish #%s kutilmagan xato bilan yiqildi", broadcast_id)
        try:
            async with AsyncSessionLocal() as session:
                # `failed_count`/`active_seconds` berilmaydi — bu yerda
                # `_run()`ning lokal o'zgaruvchilariga kirish yo'q, DB'dagi
                # OXIRGI checkpointdagi haqiqiy sonlar nolga tushirilmasin.
                await BroadcastRepository(session).fail_broadcast(
                    broadcast_id, error="Kutilmagan ichki xato — loglarga qarang"
                )
        except Exception:
            logger.exception("Tarqatish #%s xato holatini yozib ham bo'lmadi", broadcast_id)
    finally:
        _unregister(broadcast_id, asyncio.current_task())


async def _run(
    bot: Bot, broadcast_id: int, progress_notify: Optional[ProgressNotify]
) -> None:
    async def notify_safe(finished: bool) -> None:
        """`progress_notify`ni xato ko'tarmaydigan qilib chaqiradi.

        DB holati (`mark_paused`/`mark_cancelled`/`finish_broadcast`/
        `fail_broadcast`) BU chaqiruvdan OLDIN allaqachon commit qilingan
        bo'ladi — agar notifikatsiya (masalan admin sessiyasi/DB o'qishi)
        xato bersa va bu yerda ushlanmasa, `_guarded_run`ning umumiy
        except bloki uni ushlab, ALLAQACHON to'g'ri yakunlangan
        tarqatishni "failed" deb QAYTA YOZIB QO'YARDI (haqiqiy natijani
        yo'qotib).
        """
        if not progress_notify:
            return
        try:
            await progress_notify(broadcast_id, finished)
        except Exception:
            logger.exception(
                "Tarqatish #%s progress_notify xato berdi (DB holati allaqachon yozilgan, ta'sir qilmaydi)",
                broadcast_id,
            )

    async with AsyncSessionLocal() as session:
        repo = BroadcastRepository(session)
        user_repo = UserRepository(session)

        bc = await repo.get(broadcast_id)
        if bc is None or bc.status != "running":
            return

        mode = bc.mode
        src_chat_id = bc.src_chat_id
        src_message_id = bc.src_message_id
        admin_id = bc.created_by

        success = bc.success_count
        failed = bc.failed_count
        cursor: Optional[int] = bc.cursor_user_id
        total = bc.total_targets
        base_active_seconds = bc.active_seconds
        segment_started = utcnow()

        limiter = RateLimiter(DEFAULT_RATE_PER_SEC)
        semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY)

        buffer: list[tuple[int, int]] = []
        exhausted = False
        pending: set[asyncio.Task] = set()
        dispatch_order: deque[int] = deque()
        done_ids: set[int] = set()

        results_since_checkpoint = 0
        last_checkpoint = utcnow()
        last_control_check = utcnow()
        last_total_refresh = utcnow()
        reached_this_round: list[int] = []
        fatal: Optional[str] = None

        def elapsed_active() -> int:
            return int(base_active_seconds + (utcnow() - segment_started).total_seconds())

        async def deliver(user_id: int, telegram_id: int) -> tuple[int, bool, Optional[Classification]]:
            async with semaphore:
                last_cls: Optional[Classification] = None
                for attempt in range(SEND_ATTEMPTS):
                    await limiter.wait()
                    try:
                        if mode == "forward":
                            await bot.forward_message(
                                chat_id=telegram_id,
                                from_chat_id=src_chat_id,
                                message_id=src_message_id,
                            )
                        else:
                            await bot.copy_message(
                                chat_id=telegram_id,
                                from_chat_id=src_chat_id,
                                message_id=src_message_id,
                            )
                        return user_id, True, None
                    except TelegramRetryAfter as exc:
                        # Klassifikatsiyani saqlab qo'yamiz — agar QOLGAN
                        # urinishlar HAM shu bilan tugasa (SEND_ATTEMPTS
                        # tugab ketsa), `last_cls` `None` bo'lib qolmasin:
                        # aks holda haqiqiy sabab "noma'lum xato" deb
                        # yozilib, flood-wait diagnostikasi yo'qolardi.
                        last_cls = classify(exc)
                        wait = max(int(getattr(exc, "retry_after", 1)), 1)
                        # Limitga bitta so'rov tegsa qolgani ham tegadi —
                        # barcha yuboruvchilarni birga kechiktiramiz.
                        limiter.pause(wait + 0.5)
                        await asyncio.sleep(wait)
                        continue
                    except Exception as exc:  # noqa: BLE001 — tasniflab qaytaramiz
                        last_cls = classify(exc)
                        if not last_cls.retriable:
                            return user_id, False, last_cls
                        await asyncio.sleep(0.5 * (attempt + 1))
                        continue
                return user_id, False, last_cls

        async def process_done(done_tasks: set) -> None:
            """Tugagan vazifalarning natijasini hisoblaydi.

            Pauza/bekor qilishda navbatdagi vazifalarni "drain" qilganda ham
            AYNAN shu funksiya orqali o'tkaziladi — aks holda o'sha paytda
            hali havoda bo'lgan yuborishlarning haqiqiy natijasi (muvaffaqiyat/
            xato, passivlashtirish) kursor faqat oldinga siljib, hisobga
            kirmasdan yo'qolib qolardi.
            """
            nonlocal success, failed, fatal, results_since_checkpoint
            for task in done_tasks:
                if task.cancelled():
                    continue
                user_id, ok, cls = task.result()
                done_ids.add(user_id)
                results_since_checkpoint += 1

                if ok:
                    success += 1
                    reached_this_round.append(user_id)
                    continue

                failed += 1
                detail = cls.detail if cls else "noma'lum"
                await repo.add_delivery(broadcast_id, user_id, "failed", detail[:200])

                if cls and cls.fatal:
                    fatal = detail
                elif cls and cls.passivate:
                    if cls.kind == Kind.BLOCKED:
                        await user_repo.mark_blocked(user_id)
                    else:
                        await user_repo.mark_unreachable(user_id)

            if reached_this_round:
                await user_repo.mark_many_unblocked(reached_this_round)
                reached_this_round.clear()

        while True:
            now = utcnow()

            if (now - last_control_check).total_seconds() >= CONTROL_CHECK_INTERVAL_SECONDS:
                last_control_check = now
                status = await repo.get_status(broadcast_id)
                if status in ("cancel_requested", "pause_requested"):
                    if pending:
                        # Timeout bilan — aks holda bitta osilib qolgan
                        # yuborish (masalan tarmoq muammosi) "bir necha
                        # soniyada to'xtaydi" degan va'dani cheksiz
                        # cho'zib yuborardi. Muddat tugasa, hali
                        # tugallanmagan urinishlar hisobga kiritilmay
                        # tashlab ketiladi (keyingi safar kursordan qayta
                        # urinilishi mumkin) — bloklanmaslik muhimroq.
                        drained, still_pending = await asyncio.wait(pending, timeout=15.0)
                        await process_done(drained)
                        if still_pending:
                            logger.warning(
                                "Tarqatish #%s: pauza/bekor qilishda %s ta yuborish "
                                "15s ichida tugamadi, hisobga olinmasdan tashlab ketildi",
                                broadcast_id, len(still_pending),
                            )
                            for task in still_pending:
                                task.cancel()
                        pending.clear()
                    cursor = _advance_watermark(dispatch_order, done_ids, cursor)
                    await session.commit()
                    if status == "cancel_requested":
                        await repo.mark_cancelled(
                            broadcast_id, success, failed, active_seconds=elapsed_active()
                        )
                    else:
                        await repo.mark_paused(
                            broadcast_id,
                            cursor_user_id=cursor,
                            active_seconds=elapsed_active(),
                            success_count=success,
                            failed_count=failed,
                            total_targets=total,
                        )
                    # DB holati yozilgach DARHOL ro'yxatdan chiqariladi —
                    # "davom ettirish" shu zahoti bosilsa ham yangi vazifa
                    # to'sqinliksiz boshlansin (izoh: `_unregister`).
                    _unregister(broadcast_id, asyncio.current_task())
                    await notify_safe(True)
                    return

            if not buffer and not exhausted:
                batch = await user_repo.fetch_broadcast_batch(
                    after_id=cursor, limit=FETCH_BATCH, exclude_user_id=admin_id
                )
                if not batch:
                    exhausted = True
                else:
                    buffer = list(batch)
                    # Yangi qo'shilgan userlar hisobga kirsin deb davriy
                    # yangilanadi — lekin HAR bufer to'lganda emas (bu
                    # to'liq COUNT(*) so'rovi; 37 ming foydalanuvchida
                    # ~190 marta chaqirilib, keraksiz DB yukini oshirardi).
                    if (now - last_total_refresh).total_seconds() >= TOTAL_REFRESH_INTERVAL_SECONDS:
                        total = await user_repo.count_broadcast_targets(exclude_user_id=admin_id)
                        last_total_refresh = now

            while len(pending) < DEFAULT_CONCURRENCY and buffer:
                user_id, telegram_id = buffer.pop(0)
                dispatch_order.append(user_id)
                pending.add(asyncio.create_task(deliver(user_id, telegram_id)))

            if not pending:
                break  # exhausted va navbatda hech kim qolmadi

            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED, timeout=2.0
            )
            await process_done(done)
            cursor = _advance_watermark(dispatch_order, done_ids, cursor)

            if fatal:
                # Xabarning o'zi/manbasi buzuq — qolgan userlarga urinish
                # behuda, darhol to'xtatamiz.
                for task in pending:
                    task.cancel()
                await session.commit()
                await repo.fail_broadcast(
                    broadcast_id, failed, error=fatal, active_seconds=elapsed_active()
                )
                await notify_safe(True)
                return

            due_by_count = results_since_checkpoint >= CHECKPOINT_EVERY_RESULTS
            due_by_time = (now - last_checkpoint).total_seconds() >= CHECKPOINT_INTERVAL_SECONDS
            if due_by_count or due_by_time:
                await session.commit()
                await repo.checkpoint(
                    broadcast_id,
                    cursor_user_id=cursor,
                    active_seconds=elapsed_active(),
                    success_count=success,
                    failed_count=failed,
                    total_targets=total,
                )
                last_checkpoint = now
                results_since_checkpoint = 0
                await notify_safe(False)

        await session.commit()
        await repo.finish_broadcast(
            broadcast_id, success, failed, active_seconds=elapsed_active()
        )
        await notify_safe(True)


def _advance_watermark(
    dispatch_order: "deque[int]", done_ids: set[int], cursor: Optional[int]
) -> Optional[int]:
    """Faqat UZLUKSIZ tugallangan boshidan kursorni siljitadi.

    `dispatch_order` — jo'natilgan tartibda navbat. Boshidan boshlab
    `done_ids` da bor ekanlarini olib tashlaydi va kursorni o'sha yergacha
    ko'taradi; birinchi hali tugallanmagan joyda to'xtaydi — o'rtadagi
    tugallanmagan foydalanuvchi hech qachon tashlab ketilmaydi.
    """
    while dispatch_order and dispatch_order[0] in done_ids:
        finished_id = dispatch_order.popleft()
        done_ids.discard(finished_id)
        cursor = finished_id
    return cursor
