"""Adminga kunlik qisqa hisobot (REJA P2.10).

Har kuni 09:00 (Asia/Tashkent) da adminlarga (`settings.ADMIN_USER_IDS`)
OLDINGI Toshkent kuni (00:00–24:00, UTC+5) bo'yicha ixcham xulosa
yuboriladi: tarjimalar va xato ulushi, provayderlar ulushi, uzun matnlar,
`local_mt` kechikishi (p50), yangi foydalanuvchilar, botni bloklaganlar,
TTS va OCR so'rovlari/xatolari, homiyliklar.

Samaradorlik: har bir jadvalga BITTA (provayderlar uchun yana bitta
GROUP BY) yig'ma so'rov, hammasi `created_at` oralig'i bo'yicha —
`translations`/`events`dagi BRIN va `(event_type, created_at)` indekslari
ishlaydi. `COUNT(*) FILTER (WHERE ...)` bir o'tishda bir nechta sonni
hisoblaydi.

Takrorlanmaslik (idempotentlik): bot kun davomida bir necha marta qayta
ishga tushishi mumkin. Har bir kun uchun Redis'da `SET NX EX` kaliti
(`daily_report:sent:<sana>`) — qaysi jarayon birinchi "egallasa" o'sha
yuboradi. Redis yo'q yoki nosoz bo'lsa — jarayon xotirasidagi to'plam:
bitta jarayon ichida kuniga ko'pi bilan bir marta (restartdan keyin
takror ketishi mumkin — bu "ko'rinmay qolish"dan yaxshiroq).

Bot 09:00 da ishlamay turgan bo'lsa, keyin ko'tarilganda (shu kun
ichida) kechikkan hisobot baribir yuboriladi — kalit hali yo'q bo'ladi.

Vaqt mintaqasi: Toshkentda yozgi vaqt yo'q, shuning uchun qat'iy UTC+5
(`panel._is_peak_hour` bilan bir xil yondashuv) — konteynerda `tzdata`
bo'lishiga bog'liq emas.

Sozlamalar `settings.py`da emas, modul konstantalari sifatida (hozircha
o'zgartirish ehtiyoji yo'q; kerak bo'lsa `.env`ga ko'chirish oson).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config.settings import settings
from bot.database.models import Donation, Event, Translation, User
from bot.services.events import EventType, utcnow
from bot.services.stats import num, percent
from bot.utils.text import html_escape

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  Sozlamalar (modul konstantalari)
# ─────────────────────────────────────────────────────────────
TASHKENT_TZ = timezone(timedelta(hours=5), "Asia/Tashkent")
REPORT_HOUR = 9  # Toshkent vaqti bilan
LONG_TEXT_CHARS = 500  # "uzun matn" chegarasi (`source_chars > ...`)
LOCAL_MT_PROVIDER = "local_mt"
REDIS_KEY_PREFIX = "daily_report:sent:"
# Kalit bir necha kun yashaydi — kechikkan qayta urinishlar ham bir xil
# kalitni ko'radi, keyin o'zi o'chadi.
REDIS_KEY_TTL_SECONDS = 3 * 24 * 60 * 60
# Fon vazifasi uzog'i bilan shuncha uxlaydi, keyin qayta tekshiradi: soat
# siljishi/uyqu rejimidan keyin ham kech qolmaslik uchun. Takror yuborish
# xavfi yo'q — kalit buni to'sadi.
MAX_SLEEP_SECONDS = 60 * 60


# ─────────────────────────────────────────────────────────────
#  Vaqt yordamchilari (sof funksiyalar — sinovda tekshiriladi)
# ─────────────────────────────────────────────────────────────
def tashkent_today(now_utc: Optional[datetime] = None) -> date:
    return (now_utc or utcnow()).astimezone(TASHKENT_TZ).date()


def previous_tashkent_day(now_utc: Optional[datetime] = None) -> date:
    return tashkent_today(now_utc) - timedelta(days=1)


def day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Toshkent kuni `[00:00, ertasi 00:00)` — UTC'da."""
    start = datetime.combine(day, time(0, 0), tzinfo=TASHKENT_TZ)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def scheduled_at_utc(day: date) -> datetime:
    """`day` kuni soat `REPORT_HOUR`:00 (Toshkent) — UTC'da."""
    return datetime.combine(day, time(REPORT_HOUR, 0), tzinfo=TASHKENT_TZ).astimezone(
        timezone.utc
    )


def is_due(now_utc: datetime) -> bool:
    """Bugungi (Toshkent) yuborish vaqti keldimi."""
    return now_utc >= scheduled_at_utc(tashkent_today(now_utc))


def seconds_until_next_check(now_utc: datetime) -> float:
    """Keyingi 09:00 gacha (uzog'i bilan `MAX_SLEEP_SECONDS`)."""
    today = tashkent_today(now_utc)
    target = scheduled_at_utc(today)
    if now_utc >= target:
        target = scheduled_at_utc(today + timedelta(days=1))
    return max(1.0, min((target - now_utc).total_seconds(), MAX_SLEEP_SECONDS))


def idempotency_key(day: date) -> str:
    return f"{REDIS_KEY_PREFIX}{day.isoformat()}"


# Redis bo'lmaganda: shu jarayonda allaqachon "egallangan" kunlar.
_local_claims: set[str] = set()


async def claim_day(redis, day: date, local: Optional[set[str]] = None) -> bool:
    """Shu kun hisobotini yuborish huquqini oladi. `True` — yuborish kerak.

    Redis bor bo'lsa — `SET NX EX` (bir nechta jarayon/restart orasida
    atomik). Redis yo'q yoki xato bersa — jarayon xotirasidagi to'plam.
    """
    local = _local_claims if local is None else local
    key = idempotency_key(day)
    if redis is not None:
        try:
            return bool(await redis.set(key, "1", ex=REDIS_KEY_TTL_SECONDS, nx=True))
        except Exception:
            logger.warning("Kunlik hisobot: Redis nosoz — xotiradagi belgiga o'tildi", exc_info=True)
    if key in local:
        return False
    local.add(key)
    return True


async def release_day(redis, day: date, local: Optional[set[str]] = None) -> None:
    """Yuborish umuman muvaffaqiyatsiz bo'lsa — keyingi tekshiruvda qayta urinish uchun."""
    local = _local_claims if local is None else local
    key = idempotency_key(day)
    local.discard(key)
    if redis is not None:
        try:
            await redis.delete(key)
        except Exception:
            logger.warning("Kunlik hisobot kalitini o'chirib bo'lmadi", exc_info=True)


# ─────────────────────────────────────────────────────────────
#  Ma'lumot yig'ish
# ─────────────────────────────────────────────────────────────
@dataclass
class DailyReport:
    day: date
    label: str = ""  # sarlavhadagi qo'shimcha (masalan "bugun, hozirgacha")

    translations_total: int = 0
    translations_success: int = 0
    providers: dict[str, int] = field(default_factory=dict)
    long_total: int = 0
    long_success: int = 0
    local_mt_p50_ms: Optional[float] = None

    ocr_total: int = 0
    ocr_failed: int = 0

    tts_requested: int = 0
    tts_failed: int = 0

    new_users: int = 0
    blocked_bot: int = 0

    donations_count: int = 0
    donations_stars: int = 0

    @property
    def translations_errors(self) -> int:
        return max(0, self.translations_total - self.translations_success)


async def collect(
    session: AsyncSession,
    day: date,
    *,
    until: Optional[datetime] = None,
) -> DailyReport:
    """`day` (Toshkent) bo'yicha yig'ma raqamlar. `until` — oraliqni
    shu lahzada kesish (`/report today` uchun "hozirgacha")."""
    start, end = day_bounds_utc(day)
    if until is not None and until < end:
        end = until
    r = DailyReport(day=day)

    in_range_tr = (Translation.created_at >= start, Translation.created_at < end)
    ok = Translation.status == "success"
    is_long = Translation.source_chars > LONG_TEXT_CHARS
    is_photo = Translation.input_kind == "photo"

    # ── Tarjimalar: bitta o'tish ──
    row = (
        await session.execute(
            select(
                func.count(Translation.id),
                func.count(Translation.id).filter(ok),
                func.count(Translation.id).filter(is_long),
                func.count(Translation.id).filter(is_long, ok),
                func.count(Translation.id).filter(is_photo),
                func.count(Translation.id).filter(is_photo, Translation.status != "success"),
            ).where(*in_range_tr)
        )
    ).one()
    (
        r.translations_total,
        r.translations_success,
        r.long_total,
        r.long_success,
        r.ocr_total,
        r.ocr_failed,
    ) = (int(v or 0) for v in row)

    # ── Provayderlar ulushi (kesh ham alohida provayder: `cache`) ──
    rows = await session.execute(
        select(Translation.provider, func.count(Translation.id))
        .where(*in_range_tr)
        .group_by(Translation.provider)
        .order_by(func.count(Translation.id).desc())
    )
    r.providers = {(p or "?"): int(n) for p, n in rows.all()}

    # ── local_mt kechikishi (median) — faqat muvaffaqiyatli, keshsiz ──
    p50 = (
        await session.execute(
            select(
                func.percentile_cont(0.5).within_group(Translation.latency_ms)
            ).where(
                *in_range_tr,
                ok,
                Translation.provider == LOCAL_MT_PROVIDER,
                Translation.latency_ms.is_not(None),
            )
        )
    ).scalar_one_or_none()
    r.local_mt_p50_ms = float(p50) if p50 is not None else None

    # ── Voqealar: bitta o'tish, `(event_type, created_at)` indeksi ──
    event_types = (
        EventType.USER_BLOCKED_BOT,
        EventType.TTS_REQUESTED,
        EventType.TTS_FAILED,
    )
    row = (
        await session.execute(
            select(
                func.count(Event.id).filter(Event.event_type == EventType.USER_BLOCKED_BOT),
                func.count(Event.id).filter(Event.event_type == EventType.TTS_REQUESTED),
                func.count(Event.id).filter(Event.event_type == EventType.TTS_FAILED),
            ).where(
                Event.event_type.in_(event_types),
                Event.created_at >= start,
                Event.created_at < end,
            )
        )
    ).one()
    r.blocked_bot, r.tts_requested, r.tts_failed = (int(v or 0) for v in row)

    # ── Yangi foydalanuvchilar ──
    r.new_users = int(
        (
            await session.execute(
                select(func.count(User.id)).where(
                    User.created_at >= start, User.created_at < end
                )
            )
        ).scalar_one()
        or 0
    )

    # ── Homiylik (o'sha kuni tushgan to'lovlar; keyin qaytarilganlar ham
    # hisobga olinadi — kun voqeasi sifatida) ──
    row = (
        await session.execute(
            select(
                func.count(Donation.id),
                func.coalesce(func.sum(Donation.stars), 0),
            ).where(Donation.created_at >= start, Donation.created_at < end)
        )
    ).one()
    r.donations_count, r.donations_stars = int(row[0] or 0), int(row[1] or 0)

    return r


# ─────────────────────────────────────────────────────────────
#  Chiqarish
# ─────────────────────────────────────────────────────────────
def _pct(value: int, total: int) -> str:
    return f"{percent(value, total):.1f}%"


def render(r: DailyReport) -> str:
    """HTML (bot standarti `parse_mode=HTML`). Bazadan kelgan har qanday
    satr (provayder nomi) escape qilinadi."""
    title = f"📅 <b>Kunlik hisobot — {html_escape(r.day.isoformat())}</b>"
    if r.label:
        title += f" <i>({html_escape(r.label)})</i>"
    lines = [title, "<i>Toshkent vaqti</i>", ""]

    total = r.translations_total
    lines.append(f"🔤 <b>Tarjimalar:</b> {num(total)}")
    lines.append(
        f"  ✅ {num(r.translations_success)} ({_pct(r.translations_success, total)})"
        f" · ❌ {num(r.translations_errors)} ({_pct(r.translations_errors, total)})"
    )
    lines.append(
        f"  📏 Uzun (&gt;{LONG_TEXT_CHARS}): {num(r.long_total)}"
        + (f" · ✅ {_pct(r.long_success, r.long_total)}" if r.long_total else "")
    )
    p50 = f"{num(round(r.local_mt_p50_ms))} ms" if r.local_mt_p50_ms is not None else "—"
    lines.append(f"  ⏱ {html_escape(LOCAL_MT_PROVIDER)} p50: {p50}")

    if r.providers:
        lines.append("🧩 <b>Provayderlar:</b>")
        for name, n in r.providers.items():
            lines.append(f"  {html_escape(name)} — {num(n)} ({_pct(n, total)})")

    lines.append("")
    lines.append(f"🖼 OCR: {num(r.ocr_total)} · ❌ {num(r.ocr_failed)}")
    lines.append(f"🔊 TTS: {num(r.tts_requested)} · ❌ {num(r.tts_failed)}")
    lines.append(f"👤 Yangi: {num(r.new_users)} · 🚫 Bloklagan: {num(r.blocked_bot)}")
    lines.append(f"⭐️ Homiylik: {num(r.donations_count)} ({num(r.donations_stars)} ⭐️)")
    return "\n".join(lines)


async def build_report_text(day: date, *, label: str = "", until: Optional[datetime] = None) -> str:
    """Alohida sessiyada yig'ib, tayyor matn qaytaradi."""
    # Import shu yerda: modul sof funksiyalari (sinov) DB sozlamasiz ham
    # import qilinishi uchun.
    from bot.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        # Bir nechta mustaqil so'rov bitta suratni ko'rsin (`stats.py` dagi
        # bilan bir xil sabab).
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        report = await collect(session, day, until=until)
    report.label = label
    return render(report)


async def send_to_admins(bot, text: str) -> int:
    """Har bir adminga yuboradi; nechtasiga yetganini qaytaradi."""
    delivered = 0
    for admin_id in settings.ADMIN_USER_IDS:
        try:
            await bot.send_message(admin_id, text)
            delivered += 1
        except Exception:
            logger.warning("Kunlik hisobotni adminga (%s) yuborib bo'lmadi", admin_id, exc_info=True)
    return delivered


async def send_daily_report_once(bot, redis, day: date) -> bool:
    """Kalitni egallab, hisobotni yuboradi. `True` — shu chaqiruv yubordi."""
    if not await claim_day(redis, day):
        return False
    try:
        text = await build_report_text(day)
        delivered = await send_to_admins(bot, text)
    except Exception:
        logger.exception("Kunlik hisobotni tayyorlab bo'lmadi (%s)", day)
        await release_day(redis, day)
        return False
    if delivered == 0:
        # Hech kimga yetmadi (tarmoq?) — keyingi tekshiruvda qayta urinamiz.
        await release_day(redis, day)
        return False
    logger.info("Kunlik hisobot yuborildi: %s (%s ta admin)", day, delivered)
    return True


async def run_daily_report_loop(bot, redis) -> None:
    """Fon vazifasi (`main.py`). Hech qachon o'zi tugamaydi; bekor
    qilinganda (`CancelledError`) toza chiqadi."""
    if not settings.ADMIN_USER_IDS:
        logger.info("Kunlik hisobot o'chiq — ADMIN_USER_IDS bo'sh")
        return
    while True:
        try:
            now = utcnow()
            if is_due(now):
                await send_daily_report_once(bot, redis, previous_tashkent_day(now))
            delay = seconds_until_next_check(utcnow())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Kunlik hisobot siklida kutilmagan xato")
            delay = MAX_SLEEP_SECONDS
        await asyncio.sleep(delay)
