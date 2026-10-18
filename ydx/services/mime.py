"""Иконки типов файлов: системная тема → наши запасные SVG."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QPolygon

from ydx.ui.widgets import icon as svg_icon

_FOLDER = None
_ICON_SIZE = 48

THEME_BY_MEDIA = {
    "image": "image-x-generic",
    "video": "video-x-generic",
    "audio": "audio-x-generic",
    "text": "text-x-generic",
    "document": "x-office-document",
    "spreadsheet": "x-office-spreadsheet",
    "presentation": "x-office-presentation",
    "compressed": "package-x-generic",
    "executable": "application-x-executable",
    "data": "application-x-archive",
    "diskimage": "media-optical",
    "font": "font-x-generic",
}


def folder_pixmap(size: int = _ICON_SIZE) -> QPixmap:
    """Жёлтая папка в стиле Диска."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size
    body = QColor("#FFCC00")
    back = QColor("#F5B800")
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(back)
    p.drawRoundedRect(int(s * 0.08), int(s * 0.20), int(s * 0.84), int(s * 0.22), 4, 4)
    p.setBrush(body)
    p.drawRoundedRect(int(s * 0.08), int(s * 0.30), int(s * 0.84), int(s * 0.58), 5, 5)
    p.end()
    return pm


def file_icon_pixmap(resource, size: int = _ICON_SIZE) -> QPixmap:
    """Иконка файла по media_type/mime: системная тема, иначе «страница»."""
    theme = THEME_BY_MEDIA.get(resource.media_type or "")
    if theme:
        sys_icon = QIcon.fromTheme(theme)
        if not sys_icon.isNull():
            return sys_icon.pixmap(size, size)
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    page = QColor("#E8E9EC")
    edge = QColor("#C9CBD2")
    m = int(size * 0.18)
    w = size - 2 * m
    fold = int(w * 0.30)
    right, top = size - m, m
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(page)
    p.drawRoundedRect(m, m, w, size - 2 * m, 5, 5)
    # «собачье ухо» — загнутый уголок страницы
    p.setBrush(edge)
    p.drawPolygon(QPolygon([
        QPoint(right - fold, top),
        QPoint(right, top + fold),
        QPoint(right - fold, top + fold),
    ]))
    ext = (resource.name.rsplit(".", 1)[-1][:4].upper() if "." in resource.name else "")
    if ext:
        font = QFont()
        font.setPixelSize(max(int(size * 0.16), 7))
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor("#7A7D87"))
        p.drawText(m, m, w, size - 2 * m, Qt.AlignmentFlag.AlignCenter, ext)
    p.end()
    return pm


def resource_pixmap(resource, size: int = _ICON_SIZE) -> QPixmap:
    if resource.is_dir:
        return folder_pixmap(size)
    return file_icon_pixmap(resource, size)
