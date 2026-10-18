"""Форматирование размеров/дат/скоростей для UI."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from ydx.services import i18n

UNITS_RU = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
UNITS_EN = ["B", "KB", "MB", "GB", "TB"]


def human_size(n: float | None) -> str:
    if not n:
        return "0 " + (UNITS_RU if i18n.current_language() == "ru" else UNITS_EN)[0]
    units = UNITS_RU if i18n.current_language() == "ru" else UNITS_EN
    value = float(n)
    for unit in units:
        if abs(value) < 1024 or unit == units[-1]:
            text = f"{value:.1f}".rstrip("0").rstrip(".")
            sep = "," if i18n.current_language() == "ru" else "."
            return text.replace(".", sep) + " " + unit
        value /= 1024
    return f"{n} B"


def human_speed(bps: float | None) -> str:
    return human_size(bps or 0) + "/с" if i18n.current_language() == "ru" else human_size(bps or 0) + "/s"


def human_date(dt: datetime | None, now: float | None = None) -> str:
    """Относительная дата как в веб-интерфейсе: «сегодня в 12:30», «5 окт 2025»."""
    if dt is None:
        return "—"
    now = now or time.time()
    local_now = datetime.fromtimestamp(now).astimezone()
    dt = dt.astimezone()
    if dt.date() == local_now.date():
        at = "в" if i18n.current_language() == "ru" else "at"
        return f"{i18n.tr('date.today')} {at} {dt:%H:%M}"
    if dt.date() == local_now.date() - timedelta(days=1):
        at = "в" if i18n.current_language() == "ru" else "at"
        return f"{i18n.tr('date.yesterday')} {at} {dt:%H:%M}"
    if abs((dt.date() - local_now.date()).days) < 300:
        return dt.strftime("%d %b %H:%M")
    return dt.strftime("%d %b %Y")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
