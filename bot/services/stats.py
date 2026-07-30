"""Admin statistikasi: ma'lumot yig'ish va Telegram uchun chiroyli chiqarish.

Nega alohida modul: statistika savollari ko'p va ular o'sib boradi, lekin
`AdminService` ning qolgan vazifalari (kanallar, tarqatish) bilan aloqasi yo'q.

Ko'rinish haqida. Telegram HTML quyidagilarni beradi va ular bu yerda ishlatiladi:
  `<pre>`                  — monoshirina blok. Ustunlarni tekislashning yagona
                             yo'li: oddiy matnda shrift proporsional va
                             probellar bilan tekislash buziladi.
  `<blockquote expandable>` — yig'iladigan blok. Uzun ro'yxatlar (til juftliklari)
                             xabarni cho'zib yubormaydi, admin xohlasa ochadi.
  `<b>`, `<i>`             — sarlavha va izoh.

Raqamlar probel bilan ajratiladi (37 457), chunki vergul ba'zi tillarda kasr
ajratgichi va chalkashtiradi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Sequence

from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import (
    Broadcast,
    Channel,
    Donation,
    Event,
    Translation,
    TtsRequest,
    User,
    UserSettings,
)
from bot.services.events import EventType, utcnow

# ─────────────────────────────────────────────────────────────
#  Ko'rinish yordamchilari
# ─────────────────────────────────────────────────────────────
FULL_BLOCK = "█"
EMPTY_BLOCK = "░"

# `<pre>` qatorining eng katta kengligi.
#
# Telefonda monoshirina matn ~30–32 belgi sig'adi. Undan uzun bo'lsa Telegram
# blokni gorizontal siljitadigan qiladi: xabar buzilmaydi, lekin bir qarashda
# to'liq ko'rinmaydi va o'ngga surish kerak bo'ladi.
#
# Shu sababdan ustunlar soni va chiziq uzunligi shu chegaraga moslangan.
# Dud sinovi har bir `<pre>` qatorini shu bo'yicha tekshiradi — keyinchalik
# ustun qo'shilsa sinov ogohlantiradi.
MAX_PRE_WIDTH = 30


def num(value: int | float | None) -> str:
    """Raqamni probel bilan ajratadi: 37457 → "37 457"."""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.1f}".replace(",", " ")
    return f"{value:,}".replace(",", " ")


def bar(value: int, total: int, width: int = 10) -> str:
    """Ulushni blok belgilar bilan chizadi.

    Nol bo'lmagan ulush hech bo'lmaganda bitta blok oladi — aks holda kichik
    lekin mavjud qiymat butunlay ko'rinmay qolardi va "umuman yo'q" degan
    noto'g'ri taassurot berardi.
    """
    if total <= 0:
        return EMPTY_BLOCK * width
    filled = round(value / total * width)
    if value > 0 and filled == 0:
        filled = 1
    filled = min(filled, width)
    return FULL_BLOCK * filled + EMPTY_BLOCK * (width - filled)


def percent(value: int, total: int) -> float:
    return (value / total * 100) if total else 0.0


def table(rows: Sequence[Sequence[str]], *, align: str = "lr", gap: int = 2) -> str:
    """`<pre>` ichiga tushadigan tekislangan jadval.

    `align` — har bir ustun uchun "l" (chapga) yoki "r" (o'ngga).

    Diqqat: bu yerga emoji solmang. `len()` emojini 1–2 belgi deb sanaydi,
    ekranda esa kengligi boshqacha bo'ladi va ustunlar siljiydi. Emoji
    sarlavhada, jadval tashqarisida turishi kerak.
    """
    if not rows:
        return ""

    columns = max(len(row) for row in rows)
    widths = [
        max(len(row[i]) if i < len(row) else 0 for row in rows) for i in range(columns)
    ]

    lines = []
    for row in rows:
        cells = []
        for i in range(columns):
            cell = row[i] if i < len(row) else ""
            how = align[i] if i < len(align) else "l"
            cells.append(cell.rjust(widths[i]) if how == "r" else cell.ljust(widths[i]))
        lines.append((" " * gap).join(cells).rstrip())
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
#  Ma'lumot
# ─────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Stats:
    users_total: int = 0
    users_by_status: dict[str, int] = field(default_factory=dict)
    users_by_lang: dict[str, int] = field(default_factory=dict)
    new_day: int = 0
    new_week: int = 0
    new_month: int = 0
    active_day: int = 0
    active_week: int = 0
    active_month: int = 0

    translations_total: int = 0
    translations_day: int = 0
    translations_week: int = 0
    avg_latency_ms: float | None = None
    cache_hits: int = 0
    cache_total: int = 0
    errors_day: int = 0
    attempts_day: int = 0
    top_pairs: list[tuple[str, str, int]] = field(default_factory=list)

    tts_total: int = 0
    tts_day: int = 0

    donations_count: int = 0
    donations_stars: int = 0
    donations_stars_month: int = 0

    support_total: int = 0
    support_day: int = 0

    events_total: int = 0
    events_day: int = 0

    active_channels: int = 0
    last_broadcast: tuple[int, str, int, int] | None = None


class StatsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _scalar(self, query) -> int:
        return (await self.session.execute(query)).scalar_one() or 0

    async def collect(self) -> Stats:
        now = utcnow()
        day = now - timedelta(days=1)
        week = now - timedelta(days=7)
        month = now - timedelta(days=30)

        s = Stats()

        # ── Foydalanuvchilar ──
        s.users_total = await self._scalar(select(func.count(User.id)))
        rows = await self.session.execute(
            select(User.status, func.count(User.id)).group_by(User.status)
        )
        s.users_by_status = {status: count for status, count in rows.all()}

        rows = await self.session.execute(
            select(UserSettings.interface_lang, func.count(UserSettings.user_id))
            .group_by(UserSettings.interface_lang)
            .order_by(func.count(UserSettings.user_id).desc())
        )
        s.users_by_lang = {lang: count for lang, count in rows.all()}

        for attr, since in (("new_day", day), ("new_week", week), ("new_month", month)):
            setattr(
                s, attr, await self._scalar(
                    select(func.count(User.id)).where(User.created_at >= since)
                )
            )
        for attr, since in (
            ("active_day", day),
            ("active_week", week),
            ("active_month", month),
        ):
            setattr(
                s, attr, await self._scalar(
                    select(func.count(User.id)).where(User.last_seen_at >= since)
                )
            )

        # ── Tarjimalar ──
        s.translations_total = await self._scalar(select(func.count(Translation.id)))
        s.translations_day = await self._scalar(
            select(func.count(Translation.id)).where(Translation.created_at >= day)
        )
        s.translations_week = await self._scalar(
            select(func.count(Translation.id)).where(Translation.created_at >= week)
        )

        # Kechikish faqat haqiqiy (keshdan kelmagan) tarjimalar bo'yicha —
        # kesh 1ms qaytaradi va o'rtachani yolg'on pasaytirardi.
        s.avg_latency_ms = (
            await self.session.execute(
                select(func.avg(Translation.latency_ms)).where(
                    Translation.created_at >= week,
                    Translation.status == "success",
                    Translation.cache_hit.is_(False),
                )
            )
        ).scalar_one_or_none()

        row = (
            await self.session.execute(
                select(
                    func.count(Translation.id),
                    func.sum(cast(Translation.cache_hit, Integer)),
                ).where(Translation.created_at >= week)
            )
        ).one()
        s.cache_total, s.cache_hits = row[0] or 0, row[1] or 0

        row = (
            await self.session.execute(
                select(
                    func.count(Translation.id),
                    func.sum(
                        case((Translation.status != "success", 1), else_=0)
                    ),
                ).where(Translation.created_at >= day)
            )
        ).one()
        s.attempts_day, s.errors_day = row[0] or 0, row[1] or 0

        rows = await self.session.execute(
            select(
                Translation.source_lang_detected,
                Translation.target_lang,
                func.count(Translation.id).label("n"),
            )
            .where(Translation.status == "success")
            .group_by(Translation.source_lang_detected, Translation.target_lang)
            .order_by(func.count(Translation.id).desc())
            .limit(8)
        )
        s.top_pairs = [(src or "?", dst, n) for src, dst, n in rows.all()]

        # ── Ovoz ──
        s.tts_total = await self._scalar(select(func.count(TtsRequest.id)))
        s.tts_day = await self._scalar(
            select(func.count(TtsRequest.id)).where(TtsRequest.created_at >= day)
        )

        # ── Homiylik ──
        s.donations_count = await self._scalar(
            select(func.count(Donation.id)).where(Donation.status == "paid")
        )
        s.donations_stars = await self._scalar(
            select(func.coalesce(func.sum(Donation.stars), 0)).where(
                Donation.status == "paid"
            )
        )
        s.donations_stars_month = await self._scalar(
            select(func.coalesce(func.sum(Donation.stars), 0)).where(
                Donation.status == "paid", Donation.created_at >= month
            )
        )

        # ── Murojaatlar ──
        s.support_total = await self._scalar(
            select(func.count(Event.id)).where(
                Event.event_type == EventType.SUPPORT_MESSAGE_SENT
            )
        )
        s.support_day = await self._scalar(
            select(func.count(Event.id)).where(
                Event.event_type == EventType.SUPPORT_MESSAGE_SENT,
                Event.created_at >= day,
            )
        )

        # ── Voqealar ──
        s.events_total = await self._scalar(select(func.count(Event.id)))
        s.events_day = await self._scalar(
            select(func.count(Event.id)).where(Event.created_at >= day)
        )

        # ── Tizim ──
        s.active_channels = await self._scalar(
            select(func.count(Channel.id)).where(Channel.is_active.is_(True))
        )
        last = (
            await self.session.execute(
                select(
                    Broadcast.id,
                    Broadcast.status,
                    Broadcast.success_count,
                    Broadcast.failed_count,
                )
                .order_by(Broadcast.id.desc())
                .limit(1)
            )
        ).one_or_none()
        if last:
            s.last_broadcast = (last[0], last[1], last[2] or 0, last[3] or 0)

        return s


# ─────────────────────────────────────────────────────────────
#  Chiqarish
# ─────────────────────────────────────────────────────────────
# Emojisiz va qisqa: bu nomlar `<pre>` jadvaliga tushadi va telefon kengligiga
# sig'ishi kerak (yuqoridagi `table` va `MAX_PRE_WIDTH` izohlariga qarang).
STATUS_LABELS = {
    "active": "Aktiv",
    "blocked_bot": "Bloklagan",
    "deleted": "O'chgan",
    "banned": "Taqiq",
}

LANG_LABELS = {"uz": "O‘zbek", "en": "Ingliz", "id": "Indonez"}


def render(s: Stats) -> str:
    """Statistikani Telegram HTML ko'rinishida qaytaradi."""
    reachable = s.users_by_status.get("active", 0)
    parts: list[str] = ["📊 <b>Statistika</b>"]

    # ── Foydalanuvchilar ──
    parts.append(
        f"\n👥 <b>Foydalanuvchilar</b> — {num(s.users_total)} ta"
    )
    status_rows = []
    for key in ("active", "blocked_bot", "deleted", "banned"):
        count = s.users_by_status.get(key, 0)
        if not count:
            continue
        status_rows.append(
            (
                STATUS_LABELS.get(key, key),
                num(count),
                bar(count, s.users_total, 5),
                f"{percent(count, s.users_total):.0f}%",
            )
        )
    parts.append(f"<pre>{table(status_rows, align='lrlr', gap=1)}</pre>")

    growth = table(
        [
            ("Yangi", f"+{num(s.new_day)}", f"+{num(s.new_week)}", f"+{num(s.new_month)}"),
            ("Faol", num(s.active_day), num(s.active_week), num(s.active_month)),
            ("", "bugun", "7 kun", "30 kun"),
        ],
        align="lrrr",
    )
    parts.append(f"<pre>{growth}</pre>")

    # ── Tarjimalar ──
    cache_pct = percent(s.cache_hits, s.cache_total)
    error_pct = percent(s.errors_day, s.attempts_day)
    per_user = (s.translations_week / s.active_week) if s.active_week else 0.0

    parts.append(f"\n🌐 <b>Tarjimalar</b> — {num(s.translations_total)} ta")
    parts.append(
        "<pre>"
        + table(
            [
                ("Bugun", num(s.translations_day)),
                ("7 kun", num(s.translations_week)),
                ("Faol userga", f"{per_user:.1f}"),
                (
                    "Tezlik",
                    f"{num(round(s.avg_latency_ms))} ms" if s.avg_latency_ms else "—",
                ),
                ("Keshdan", f"{cache_pct:.0f}%"),
                ("Xato (bugun)", f"{error_pct:.1f}%"),
            ]
        )
        + "</pre>"
    )

    # ── Til juftliklari (yig'iladigan) ──
    if s.top_pairs:
        top = s.top_pairs[0][2]
        pair_rows = [
            (f"{src} → {dst}", bar(n, top, 8), num(n)) for src, dst, n in s.top_pairs
        ]
        parts.append(
            "<blockquote expandable>🔝 <b>Ommabop yo'nalishlar</b>\n"
            f"<pre>{table(pair_rows, align='llr')}</pre></blockquote>"
        )

    # ── Interfeys tillari ──
    if s.users_by_lang:
        lang_rows = [
            (
                LANG_LABELS.get(code, code),
                num(count),
                bar(count, s.users_total, 5),
                f"{percent(count, s.users_total):.0f}%",
            )
            for code, count in s.users_by_lang.items()
        ]
        parts.append(
            "<blockquote expandable>🗣 <b>Interfeys tillari</b>\n"
            f"<pre>{table(lang_rows, align='lrlr', gap=1)}</pre></blockquote>"
        )

    # ── Qolgan bo'limlar ──
    # Ikki ustun: uch ustunli variant telefon kengligidan oshib ketardi.
    # "⭐" emas, "Stars": emoji `<pre>` ichida ustunni siljitadi.
    other = [
        ("Ovoz", num(s.tts_total)),
        ("Ovoz bugun", num(s.tts_day)),
        ("Stars", num(s.donations_stars)),
        ("Stars 30 kun", num(s.donations_stars_month)),
        ("Homiylik soni", num(s.donations_count)),
        ("Murojaat", num(s.support_total)),
        ("Murojaat bugun", num(s.support_day)),
        ("Voqealar", num(s.events_total)),
        ("Voqea bugun", num(s.events_day)),
        ("Kanallar", num(s.active_channels)),
    ]
    parts.append(
        "<blockquote expandable>📦 <b>Qolgan ko'rsatkichlar</b>\n"
        f"<pre>{table(other, align='lr')}</pre></blockquote>"
    )

    if s.last_broadcast:
        bid, status, ok, bad = s.last_broadcast
        parts.append(
            f"<i>Oxirgi tarqatish #{bid} — {status}: "
            f"{num(ok)} yetdi, {num(bad)} yetmadi</i>"
        )

    parts.append(f"\n<i>Yetib boradigan baza: <b>{num(reachable)}</b> ta</i>")
    return "\n".join(parts)
