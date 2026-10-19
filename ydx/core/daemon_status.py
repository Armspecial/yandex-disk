"""Разбор вывода `yandex-disk status` (чистые функции, тестируются без Qt).

Формат (по выводу демона и yandex-disk-indicator):

    Synchronization core status: idle
    Sync progress: 45% (10.5 MB/s)
    Last synchronized items:
      file: 'photo.jpg'
      dir: 'Docs'
    Total: 26843545600
    Used: 12884901888
    Free: 13958643712
    Trash size: 1073741824

Пустой вывод — демон не запущен.
"""

from __future__ import annotations

import re

from ydx.core.models import DaemonStatus

_STATUS_MAP = {
    "idle": "idle",
    "busy": "busy",
    "paused": "paused",
    "index": "index",
    "no internet access": "no-internet",
}

_SIZE_RE = re.compile(r"^([\d\s.,]+)\s*(B|KB|MB|GB|TB)?$", re.I)
_MULT = {"": 1, "B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}


def parse_size(text: str) -> int:
    m = _SIZE_RE.match(text.strip())
    if not m:
        return 0
    number = m.group(1).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        value = float(number)
    except ValueError:
        return 0
    return int(value * _MULT[(m.group(2) or "").upper()])


def parse_status(raw: str) -> DaemonStatus:
    status = DaemonStatus(raw=raw)
    if not raw.strip():
        status.state = "stopped"
        return status

    lines = [ln.strip() for ln in raw.splitlines()]
    in_last_items = False
    for line in lines:
        if line.startswith("Synchronization core status:"):
            value = line.split(":", 1)[1].strip().lower()
            status.state = _STATUS_MAP.get(value, value or "unknown")
            in_last_items = False
        elif line.startswith("Sync progress:"):
            status.progress = line.split(":", 1)[1].strip()
            in_last_items = False
        elif line.startswith("Last synchronized items:"):
            in_last_items = True
        elif line.startswith("Error:"):
            status.state = "error"
            status.progress = line.split(":", 1)[1].strip()
            in_last_items = False
        elif in_last_items:
            m = re.match(r"(?:file|dir):\s*'(.*)'\s*$", line)
            if m and len(status.last_items) < 20:
                status.last_items.append(m.group(1))
        elif line.startswith("Total:"):
            status.total = parse_size(line.split(":", 1)[1])
        elif line.startswith("Used:"):
            status.used = parse_size(line.split(":", 1)[1])
        elif line.startswith("Free:"):
            status.free = parse_size(line.split(":", 1)[1])
        elif line.startswith("Trash size:"):
            status.trash = parse_size(line.split(":", 1)[1])
        elif line.startswith("Path:"):
            status.progress = status.progress or line.split(":", 1)[1].strip()
    return status


PAYWALL_MARKERS = ("360", "subscription", "paid", "платн", "подписк")


def looks_like_paywall(status: DaemonStatus) -> bool:
    """Эвристика: ошибка демона, похожая на требование подписки Яндекс 360."""
    if status.state != "error":
        return False
    text = (status.progress + " " + status.raw).lower()
    return any(marker in text for marker in PAYWALL_MARKERS)
