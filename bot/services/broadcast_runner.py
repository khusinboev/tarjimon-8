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
paytida ABADIY tashlab ketiladi. Shuning uchun `_DispatchTracker` jo'natish
tartibini saqlab, faqat UZLUKSIZ tugallangan boshidan kursorni siljitadi —
o'rtada tugallanmagan bo'lsa kursor o'sha yerda to'xtaydi.

## Ikki xil "qayerdaman": watermark va fetch-chegara (2026-11 tahlili)

Tarqatish #64 (2026-10-19) 29 ming nishon uchun ~2,98 mln "failed" qatori
yozdi: ~128 foydalanuvchiga ~24 000 martadan. Sabab — keyingi bufer HAR
SAFAR watermark KURSORDAN (`id > cursor`) olinardi. Watermark esa hali
tugamagan yuborishlar orqasida qoladi, shuning uchun har bufer to'ldirishda
havodagi (va allaqachon tugagan, lekin hali kursor yetmagan) ~10-15
foydalanuvchi QAYTA jo'natilardi. Takror jo'natilgan foydalanuvchi
`done_ids` to'plamiga bir marta tushadi; birinchi nusxasi kursordan
o'tganda to'plamdan o'chiriladi — ikkinchi nusxasi navbat boshida
"tugallanmagan" bo'lib ABADIY qoladi. Kursor muzlaydi, bufer esa o'sha
200 ta foydalanuvchini qayta-qayta oladi: #64 da 4 kun, 15/sek tezlikda
(#62: ~60×, #65: ~218×, #67: ~100× — bir xil iz).

Yechim: bufer `fetch_after` (oxirgi OLINGAN id — faqat oldinga yuradi)
dan olinadi, DB'ga esa watermark yoziladi. Bir ishga tushirish ichida har
foydalanuvchi faqat bir marta jo'natiladi (`claim`); DB qatori ham
`(broadcast, user)` bo'yicha ko'pi bilan bitta (`add_delivery`). Pauza/
restartdan keyin esa watermarkdan davom etadi — havoda qolgan ≤10
foydalanuvchi qayta urinilishi mumkin ("at-least-once", chegaralangan).
Qo'shimcha to'siqlar: tizimli xato zanjirida va natija soni nishondan
oshib ketganda tarqatish o'zini avtomatik pauza qilib, adminga xabar beradi.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Awaitable, Callable, Optional

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter

from bot.database.redis import get_redis
from bot.database.repositories.broadcast_repository import BroadcastRepository
from bot.database.repositories.user_repository import UserRepository
from bot.database.session import AsyncSessionLocal
from bot.services.admin_alerts import alert_admins_once
from bot.services.broadcast import DEFAULT_CONCURRENCY, DEFAULT_RATE_PER_SEC, RateLimiter
from bot.services.broadcast_errors import Classification, Kind, classify
from bot.services.events import utcnow
from bot.utils.text import html_escape

logger = logging.getLogger(__name__)

FETCH_BATCH = 200
SEND_ATTEMPTS = 2
# Bitta yuborishga qattiq muddat. aiogram sessiyasining o'z timeout'i bor,
# lekin bu yerda alohida to'siq turadi: bitta osilib qolgan so'rov
# watermarkni (demak restartdan keyingi davom nuqtasini) cheksiz
# ushlab turmasin. Muddat tugasa `TimeoutError` -> tarmoq xatosi (qayta
# uriladi, passiv qilinmaydi).
SEND_TIMEOUT_SECONDS = 90.0
# Telegram 429 `retry_after` ba'zan bir necha ming soniya bo'ladi — bitta
# yuboruvchi shuncha uxlab qolmasin. Chegaradan oshsa shu qadar kutib,
# keyin qayta urinadi; urinishlar tugasa "rate_limit" xatosi yoziladi va
# tizimli zanjir hisobiga kiradi (quyida).
MAX_RETRY_AFTER_SECONDS = 120
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
# Pauza/bekor/avto-pauzada havodagi yuborishlarni kutish muddati.
DRAIN_TIMEOUT_SECONDS = 15.0
# Tizimli xato zanjiri: shuncha KETMA-KET yuborish tarmoq/Telegram server/
# flood/noma'lum xato bilan tugasa — muammo foydalanuvchida emas, bizda
# yoki Telegramda. Davom etish behuda (hamma "failed" bo'lib yoziladi),
# tarqatish o'zini pauza qiladi, admin tekshirib "Davom ettirish" bosadi.
# Bloklagan/o'chirilgan foydalanuvchilar zanjirni UZADI — Telegram javob
# bergan, demak tizim ishlayapti (nishonlarning ~45% i bloklagan, bu
# normal holat).
SYSTEMIC_FAILURE_STREAK = 50
# Aylanish himoyasi: bir ishga tushirishda natija soni taxminiy jamidan
# shu qadar oshsa — quvurda yana qandaydir takrorlash bor. #64 holatida bu
# to'siq 4 kun o'rniga ~2 daqiqada to'xtatgan bo'lardi.
OVERRUN_MARGIN = 2 * FETCH_BATCH + 1000

_SYSTEMIC_KINDS = frozenset({Kind.NETWORK, Kind.SERVER, Kind.RATE_LIMIT, Kind.UNKNOWN})

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


class _DispatchTracker:
    """Bir ishga tushirish ichida "kim jo'natildi, kim tugadi, kursor qayerda".

    Uch alohida tushuncha — ularni aralashtirish #64 ning asl sababi edi:

    - `cursor`      — watermark: uzluksiz tugallangan prefiksning oxiri.
                      DB'ga yoziladi, pauza/restartdan keyin shu yerdan davom.
    - `fetch_after` — keyingi bufer QAYERDAN olinadi: oxirgi OLINGAN id.
                      Faqat oldinga yuradi, tugashini kutmaydi — shuning
                      uchun bir foydalanuvchi bir ishga tushirishda ikki
                      marta buferga tushmaydi.
    - `_claimed`    — shu ishga tushirishda jo'natilganlar. `claim()`
                      ikkinchi urinishni rad etadi — fetch mantig'ida
                      xato bo'lsa ham takror yuborilmaydi (va bu
                      `duplicates_skipped`da ko'rinadi, logga tushadi).
    """

    def __init__(self, cursor: Optional[int]) -> None:
        self.cursor = cursor
        self.fetch_after = cursor
        self._order: deque[int] = deque()
        self._done: set[int] = set()
        self._claimed: set[int] = set()
        self.duplicates_skipped = 0

    def note_fetched(self, batch: list[tuple[int, int]]) -> None:
        """Bufer olindi — chegara oxirgi olingan id'ga ko'tariladi (so'rov
        `ORDER BY id`, demak oxirgisi eng kattasi)."""
        if batch:
            last_id = batch[-1][0]
            if self.fetch_after is None or last_id > self.fetch_after:
                self.fetch_after = last_id

    def claim(self, user_id: int) -> bool:
        """`True` — jo'natish mumkin; `False` — shu ishga tushirishda
        allaqachon jo'natilgan, tashlab ketiladi."""
        if user_id in self._claimed:
            self.duplicates_skipped += 1
            return False
        self._claimed.add(user_id)
        self._order.append(user_id)
        return True

    def complete(self, user_id: int) -> None:
        self._done.add(user_id)
        self.cursor = _advance_watermark(self._order, self._done, self.cursor)

    @property
    def in_flight(self) -> int:
        """Jo'natilgan, lekin kursordan hali o'tmagan foydalanuvchilar."""
        return len(self._order)


class _SystemicFailureGuard:
    """Ketma-ket TIZIMLI xatolarni sanaydi (tarmoq, Telegram 5xx, flood,
    noma'lum). Muvaffaqiyat yoki passiv xato (bloklagan, o'chirilgan —
    Telegram to'g'ri javob bergan) zanjirni uzadi."""

    def __init__(self, threshold: int = SYSTEMIC_FAILURE_STREAK) -> None:
        self.threshold = threshold
        self.streak = 0
        self.last_detail: Optional[str] = None

    def record_success(self) -> None:
        self.streak = 0

    def record_failure(self, cls: Optional[Classification]) -> bool:
        """`True` — chegara urildi, tarqatishni to'xtatish kerak."""
        kind = cls.kind if cls is not None else Kind.UNKNOWN
        if kind not in _SYSTEMIC_KINDS:
            self.streak = 0
            return False
        self.streak += 1
        self.last_detail = cls.detail if cls is not None else "noma'lum"
        return self.streak >= self.threshold


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


async def _alert_auto_pause(bot: Bot, broadcast_id: int, reason: str) -> None:
    """Avtomatik pauza haqida adminlarga (dedup bilan) xabar. Xato
    ko'tarmaydi — DB holati allaqachon yozilgan, bu faqat bildirishnoma."""
    try:
        redis = get_redis()
    except Exception:  # noqa: BLE001 — Redis bo'lmasa dedup'siz yuboriladi
        redis = None
    text = (
        f"⏸ <b>Tarqatish #{broadcast_id} avtomatik pauza qilindi.</b>\n\n"
        f"{html_escape(reason)}\n\n"
        "Sababini tekshirib, \"📤 Reklama\" bo'limida \"▶️ Davom ettirish\" "
        "yoki \"⛔ Bekor qilish\"ni tanlang."
    )
    try:
        await alert_admins_once(bot, redis, f"broadcast_autopause:{broadcast_id}", text)
    except Exception:
        logger.exception("Tarqatish #%s avto-pauza haqida xabar berib bo'lmadi", broadcast_id)


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
        total = bc.total_targets
        base_active_seconds = bc.active_seconds
        segment_started = utcnow()

        limiter = RateLimiter(DEFAULT_RATE_PER_SEC)
        semaphore = asyncio.Semaphore(DEFAULT_CONCURRENCY)

        tracker = _DispatchTracker(bc.cursor_user_id)
        guard = _SystemicFailureGuard()
        buffer: list[tuple[int, int]] = []
        exhausted = False
        pending: set[asyncio.Task] = set()

        results_since_checkpoint = 0
        results_this_run = 0
        last_checkpoint = utcnow()
        last_control_check = utcnow()
        last_total_refresh = utcnow()
        reached_this_round: list[int] = []
        fatal: Optional[str] = None
        # Avtomatik pauza sababi — to'ldirilsa quvur o'zini to'xtatadi.
        auto_pause: Optional[str] = None

        def elapsed_active() -> int:
            return int(base_active_seconds + (utcnow() - segment_started).total_seconds())

        async def send_once(telegram_id: int) -> None:
            if mode == "forward":
                coro = bot.forward_message(
                    chat_id=telegram_id, from_chat_id=src_chat_id, message_id=src_message_id,
                )
            else:
                coro = bot.copy_message(
                    chat_id=telegram_id, from_chat_id=src_chat_id, message_id=src_message_id,
                )
            await asyncio.wait_for(coro, timeout=SEND_TIMEOUT_SECONDS)

        async def deliver(user_id: int, telegram_id: int) -> tuple[int, bool, Optional[Classification]]:
            async with semaphore:
                last_cls: Optional[Classification] = None
                for attempt in range(SEND_ATTEMPTS):
                    await limiter.wait()
                    try:
                        await send_once(telegram_id)
                        return user_id, True, None
                    except TelegramRetryAfter as exc:
                        # Klassifikatsiyani saqlab qo'yamiz — agar QOLGAN
                        # urinishlar HAM shu bilan tugasa (SEND_ATTEMPTS
                        # tugab ketsa), `last_cls` `None` bo'lib qolmasin:
                        # aks holda haqiqiy sabab "noma'lum xato" deb
                        # yozilib, flood-wait diagnostikasi yo'qolardi.
                        last_cls = classify(exc)
                        wait = min(
                            max(int(getattr(exc, "retry_after", 1) or 1), 1),
                            MAX_RETRY_AFTER_SECONDS,
                        )
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
            nonlocal success, failed, fatal, auto_pause
            nonlocal results_since_checkpoint, results_this_run
            for task in done_tasks:
                if task.cancelled():
                    continue
                user_id, ok, cls = task.result()
                tracker.complete(user_id)
                results_since_checkpoint += 1
                results_this_run += 1

                if ok:
                    success += 1
                    guard.record_success()
                    reached_this_round.append(user_id)
                    continue

                detail = cls.detail if cls else "noma'lum"
                # Pauza/restartdan keyin qayta urinilgan foydalanuvchi uchun
                # qator allaqachon bor bo'lishi mumkin — u holda hisob ham
                # oshirilmaydi: `failed_count` har doim qatorlar soniga teng.
                if await repo.add_delivery(broadcast_id, user_id, "failed", detail[:200]):
                    failed += 1

                if cls and cls.fatal:
                    fatal = detail
                elif cls and cls.passivate:
                    if cls.kind == Kind.BLOCKED:
                        await user_repo.mark_blocked(user_id)
                    else:
                        await user_repo.mark_unreachable(user_id)

                if guard.record_failure(cls) and auto_pause is None:
                    auto_pause = (
                        f"{guard.streak} ta ketma-ket yuborish tizimli xato bilan tugadi "
                        f"(tarmoq/Telegram/flood). Oxirgisi: {detail[:200]}"
                    )

            if reached_this_round:
                await user_repo.mark_many_unblocked(reached_this_round)
                reached_this_round.clear()

        async def drain_pending() -> None:
            """Havodagi yuborishlarni (muddat bilan) kutib, hisobga oladi.

            Muddat tugasa, hali tugallanmagan urinishlar hisobga kiritilmay
            tashlab ketiladi (keyingi safar kursordan qayta urinilishi
            mumkin) — bloklanmaslik muhimroq. Aks holda bitta osilib qolgan
            yuborish "bir necha soniyada to'xtaydi" degan va'dani cheksiz
            cho'zib yuborardi.
            """
            if not pending:
                return
            drained, still_pending = await asyncio.wait(pending, timeout=DRAIN_TIMEOUT_SECONDS)
            await process_done(drained)
            if still_pending:
                logger.warning(
                    "Tarqatish #%s: to'xtatishda %s ta yuborish %.0fs ichida tugamadi, "
                    "hisobga olinmasdan tashlab ketildi",
                    broadcast_id, len(still_pending), DRAIN_TIMEOUT_SECONDS,
                )
                for task in still_pending:
                    task.cancel()
            pending.clear()

        async def stop_paused(reason: Optional[str]) -> None:
            """Tarqatishni `paused`ga o'tkazib, vazifani yakunlaydi.
            `reason` — avtomatik pauza sababi (kartada ko'rinadi, adminga
            alohida xabar ketadi); admin o'zi so'ragan pauzada `None`."""
            await drain_pending()
            await session.commit()
            await repo.mark_paused(
                broadcast_id,
                cursor_user_id=tracker.cursor,
                active_seconds=elapsed_active(),
                success_count=success,
                failed_count=failed,
                total_targets=total,
                error=reason,
            )
            # DB holati yozilgach DARHOL ro'yxatdan chiqariladi —
            # "davom ettirish" shu zahoti bosilsa ham yangi vazifa
            # to'sqinliksiz boshlansin (izoh: `_unregister`).
            _unregister(broadcast_id, asyncio.current_task())
            if reason is not None:
                logger.warning("Tarqatish #%s avtomatik pauza: %s", broadcast_id, reason)
                await _alert_auto_pause(bot, broadcast_id, reason)
            await notify_safe(True)

        while True:
            now = utcnow()

            if (now - last_control_check).total_seconds() >= CONTROL_CHECK_INTERVAL_SECONDS:
                last_control_check = now
                status = await repo.get_status(broadcast_id)
                if status == "cancel_requested":
                    await drain_pending()
                    await session.commit()
                    await repo.mark_cancelled(
                        broadcast_id, success, failed, active_seconds=elapsed_active()
                    )
                    _unregister(broadcast_id, asyncio.current_task())
                    await notify_safe(True)
                    return
                if status == "pause_requested":
                    await stop_paused(None)
                    return

            if not buffer and not exhausted:
                # DIQQAT: `tracker.fetch_after` (oxirgi OLINGAN id), watermark
                # kursor EMAS — aks holda havodagi foydalanuvchilar har bufer
                # to'ldirishda qayta olinardi (#64 aylanishining ildizi).
                batch = await user_repo.fetch_broadcast_batch(
                    after_id=tracker.fetch_after, limit=FETCH_BATCH, exclude_user_id=admin_id
                )
                if not batch:
                    exhausted = True
                else:
                    tracker.note_fetched(batch)
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
                if not tracker.claim(user_id):
                    continue
                pending.add(asyncio.create_task(deliver(user_id, telegram_id)))

            if not pending:
                if not exhausted:
                    continue  # bufer bo'shadi (masalan hammasi takror edi) — keyingisi olinsin
                break  # exhausted va navbatda hech kim qolmadi

            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED, timeout=2.0
            )
            await process_done(done)

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

            if auto_pause is None and total > 0 and results_this_run > total + OVERRUN_MARGIN:
                auto_pause = (
                    f"Aylanish himoyasi: bir ishga tushirishda {results_this_run} ta natija, "
                    f"taxminiy jami esa {total} — quvurda takrorlash bor, loglarni tekshiring."
                )
            if auto_pause is not None:
                await stop_paused(auto_pause)
                return

            due_by_count = results_since_checkpoint >= CHECKPOINT_EVERY_RESULTS
            due_by_time = (now - last_checkpoint).total_seconds() >= CHECKPOINT_INTERVAL_SECONDS
            if due_by_count or due_by_time:
                await session.commit()
                await repo.checkpoint(
                    broadcast_id,
                    cursor_user_id=tracker.cursor,
                    active_seconds=elapsed_active(),
                    success_count=success,
                    failed_count=failed,
                    total_targets=total,
                )
                last_checkpoint = now
                results_since_checkpoint = 0
                await notify_safe(False)

        if tracker.duplicates_skipped:
            # Bo'lmasligi kerak — bo'lsa fetch mantig'ida yana xato bor,
            # lekin takror yuborilmagan (claim rad etgan).
            logger.warning(
                "Tarqatish #%s: %s ta takror jo'natish urinishi rad etildi",
                broadcast_id, tracker.duplicates_skipped,
            )
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

    DIQQAT: navbatda bir id IKKI marta bo'lmasligi kerak (`_DispatchTracker.
    claim` buni kafolatlaydi) — aks holda birinchi nusxa o'tganda id
    `done_ids`dan o'chirilib, ikkinchi nusxa abadiy "tugallanmagan" qolardi
    (#64 muzlashi).
    """
    while dispatch_order and dispatch_order[0] in done_ids:
        finished_id = dispatch_order.popleft()
        done_ids.discard(finished_id)
        cursor = finished_id
    return cursor
