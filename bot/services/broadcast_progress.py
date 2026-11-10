"""Tarqatish holatini matn ko'rinishida ko'rsatish.

manager-bot (github.com/khusinboev/manager-bot, `bot/services/progress.py`)
loyihasidan o'rganib moslashtirildi.
"""

from __future__ import annotations

from datetime import datetime, timezone

from bot.database.models import Broadcast
from bot.utils.formatters import format_datetime

_STATUS_LABEL = {
    "created": "✏️ tayyorlanmoqda",
    "running": "🚀 ketmoqda",
    "pause_requested": "⏸ pauza qilinmoqda…",
    "paused": "⏸ pauzada",
    "cancel_requested": "⛔ bekor qilinmoqda…",
    "cancelled": "⛔ bekor qilindi",
    "completed": "✅ yakunlandi",
    "failed": "🚨 xato",
}

ACTIVE_STATUSES = ("running", "pause_requested", "paused", "cancel_requested")


def status_label(status: str) -> str:
    return _STATUS_LABEL.get(status, status)


def human_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} daq {sec:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} s {minutes:02d} daq"


def human_number(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def _bar(percent: float, width: int = 16) -> str:
    filled = int(round(percent / 100 * width))
    filled = max(0, min(width, filled))
    return "▓" * filled + "░" * (width - filled)


def _speed(bc: Broadcast) -> float | None:
    """Haqiqiy tezlik: yuborilganlar / SARFLANGAN vaqt.

    `active_seconds` — tarqatish `running` holatda turgan vaqt, pauza
    ICHIDA emas. Xom `finished_at - started_at` ishlatilsa, pauza qilingan
    tarqatish sun'iy ravishda "juda sekin" ko'rinardi.
    """
    active = float(bc.active_seconds or 0)
    done = bc.success_count + bc.failed_count
    if active < 1 or done == 0:
        return None
    return done / active


def render_card(bc: Broadcast) -> str:
    """Faol (yoki hozirgina tugagan) bitta tarqatishning to'liq kartasi."""
    done = bc.success_count + bc.failed_count
    total = max(bc.total_targets, done)
    percent = (done / total * 100) if total else 0.0
    active = float(bc.active_seconds or 0)

    lines = [f"📢 <b>Tarqatish #{bc.id}</b> · {status_label(bc.status)}"]

    if bc.status in ACTIVE_STATUSES or bc.status == "completed":
        lines.append(f"{_bar(percent)}  {percent:.0f}%")

    lines += [
        "",
        f"✅ Yetdi          <b>{human_number(bc.success_count)}</b>",
    ]
    if bc.failed_count:
        lines.append(f"⚠️ Yetmadi        {human_number(bc.failed_count)}")
    if bc.total_targets:
        lines.append(f"📦 Taxminiy jami  {human_number(bc.total_targets)}")

    if active >= 1:
        lines.append(f"⏱ Yuborish vaqti  <b>{human_duration(active)}</b>")

    speed = _speed(bc)
    if speed:
        lines.append(f"⚡️ Tezlik         {speed:.1f}/s".replace(".", ","))
        if bc.status == "running":
            remaining = max(0, total - done)
            if speed > 0.01:
                lines.append(f"🕒 Qolgan         {human_duration(remaining / speed)}")

    if bc.started_at:
        lines.append(f"▶️ Boshlandi      {format_datetime(bc.started_at)}")
    if bc.finished_at:
        lines.append(f"🏁 Tugadi         {format_datetime(bc.finished_at)}")

    if bc.error:
        lines += ["", f"🚨 <i>{bc.error[:300]}</i>"]

    return "\n".join(lines)


def render_history_line(bc: Broadcast) -> str:
    done = bc.success_count + bc.failed_count
    total = max(bc.total_targets, done)
    percent = (done / total * 100) if total else 0.0
    active = float(bc.active_seconds or 0)

    head = f"<b>#{bc.id}</b> · {status_label(bc.status)} · {percent:.0f}%"
    parts = [f"✅ {human_number(bc.success_count)}"]
    if bc.failed_count:
        parts.append(f"⚠️ {human_number(bc.failed_count)}")
    if active >= 1:
        parts.append(f"⏱ {human_duration(active)}")
    speed = _speed(bc)
    if speed:
        parts.append(f"⚡️ {speed:.1f}/s".replace(".", ","))

    when = ""
    if bc.started_at:
        when = f"\n   ▶️ {format_datetime(bc.started_at)}"

    return f"{head}\n   " + " · ".join(parts) + when
