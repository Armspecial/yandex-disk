"""Мелкие переиспользуемые виджеты и фабрики."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QLabel, QWidget

ICONS_DIR = Path(__file__).parent.parent / "resources" / "icons"

_icon_cache: dict[str, QIcon] = {}


def icon(name: str) -> QIcon:
    """Иконка по имени файла (без расширения) из resources/icons."""
    if name not in _icon_cache:
        path = ICONS_DIR / f"{name}.svg"
        _icon_cache[name] = QIcon(str(path)) if path.exists() else QIcon()
    return _icon_cache[name]


def make_avatar(text: str, color: str, size: int = 36, dpr: float = 2.0) -> QPixmap:
    """Круглый аватар с инициалами (у Диска нет API-аватаров). Рисуем в 2x."""
    px = int(size * dpr)
    pm = QPixmap(px, px)
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(0, 0, size, size)
    initials = "".join(w[0] for w in text.split()[:2]).upper() or "?"
    font = QFont()
    font.setPixelSize(max(int(size * 0.38), 8))
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("#FFFFFF"))
    painter.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, initials)
    painter.end()
    return pm


def elided_label(text: str, width: int, font: QFont) -> str:
    metrics = QFontMetrics(font)
    return metrics.elidedText(text, Qt.TextElideMode.ElideRight, max(width, 10))


class ElidedLabel(QLabel):
    """QLabel, обрезающий текст с многоточием под ширину виджета."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._full = text

    def setText(self, text: str) -> None:  # noqa: N802 — Qt API
        self._full = text
        super().setText(text)

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt API
        super().resizeEvent(event)
        metrics = QFontMetrics(self.font())
        super().setText(metrics.elidedText(
            self._full, Qt.TextElideMode.ElideMiddle, self.width()
        ))
