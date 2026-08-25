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
CHECKPOINT_EVERY_RESULTS = 20
CHECKPOINT_INTERVAL_SECONDS = 5.0
CONTROL_CHECK_INTERVAL_SECONDS = 2.0

ProgressNotify = Callable[[int, bool], Awaitable[None]]

# Jarayon ichida: broadcast_id -> vazifa. Bitta tarqatish ikki marta
# ishga tushmasligini kafolatlaydi (masalan admin "Yangilash"ni tez-tez
# bossa ham).
_active_tasks: dict[int, asyncio.Task] = {}


def is_running(broadcast_id: int) -> bool:
    task = _active_tasks.get(broadcast_id)
    return task is not None and not task.done()


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
                await BroadcastRepository(session).fail_broadcast(
                    broadcast_id, 0, error="Kutilmagan ichki xato — loglarga qarang"
                )
        except Exception:
            logger.exception("Tarqatish #%s xato holatini yozib ham bo'lmadi", broadcast_id)
    finally:
        _active_tasks.pop(broadcast_id, None)


async def _run(
    bot: Bot, broadcast_id: int, progress_notify: Optional[ProgressNotify]
) -> None:
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
                        drained, _ = await asyncio.wait(pending)
                        await process_done(drained)
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
                        )
                    if progress_notify:
                        await progress_notify(broadcast_id, True)
                    return

            if not buffer and not exhausted:
                batch = await user_repo.fetch_broadcast_batch(
                    after_id=cursor, limit=FETCH_BATCH, exclude_user_id=admin_id
                )
                if not batch:
                    exhausted = True
                else:
                    buffer = list(batch)
                    # Yangi qo'shilgan userlar hisobga kirsin deb davriy yangilanadi.
                    total = await user_repo.count_broadcast_targets(exclude_user_id=admin_id)

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
                if progress_notify:
                    await progress_notify(broadcast_id, True)
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
                if progress_notify:
                    await progress_notify(broadcast_id, False)

        await session.commit()
        await repo.finish_broadcast(
            broadcast_id, success, failed, active_seconds=elapsed_active()
        )
        if progress_notify:
            await progress_notify(broadcast_id, True)


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
