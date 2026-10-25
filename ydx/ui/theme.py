"""Применение темы (светлая/тёмная/системная) через QSS + токены цветов.

Дизайн-система «тёмная комната»: глубокий графитовый холст — весь цвет
достаётся фотографиям; поверхности слоями (холст → панель → карточка) с
тонкими границами-волосками; один акцент — жёлтый «затвор» — только на
главных действиях, выделении и фокусе.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

QSS_DIR = Path(__file__).parent.parent / "resources" / "qss"

# Токены тем. Имена — контракт: виды берут цвета отсюда, а не хардкодом.
DARK_COLORS: dict[str, str] = {
    "canvas": "#0E0F11",            # холст под контентом
    "panel": "#141518",             # сайдбар
    "surface": "#1C1E22",           # карточки, поля, кнопки
    "surface_hover": "#24262B",
    "surface_active": "#2B2E34",
    "hairline": "#2A2D33",          # границы-волоски
    "text": "#EDEEF0",
    "text_secondary": "#A6A9B0",
    "text_tertiary": "#71757D",
    "accent": "#FFD21E",            # жёлтый «затвор» — единственный акцент
    "accent_hover": "#FFDC4D",
    "on_accent": "#141518",
    "danger": "#FF6A4D",
    "success": "#2FB559",
    "info": "#3E8BFF",
}

LIGHT_COLORS: dict[str, str] = {
    "canvas": "#F5F5F4",
    "panel": "#FFFFFF",
    "surface": "#FFFFFF",
    "surface_hover": "#EDEDEB",
    "surface_active": "#E4E4E2",
    "hairline": "#E1E2E6",
    "text": "#1A1B1E",
    "text_secondary": "#5F636B",
    "text_tertiary": "#8B8F98",
    "accent": "#FFD21E",
    "accent_hover": "#FFC400",
    "on_accent": "#141518",
    "danger": "#E0462B",
    "success": "#1F9D4D",
    "info": "#2563D9",
}


def system_is_dark(app: QApplication) -> bool:
    color = app.palette().color(QPalette.ColorRole.Window)
    return color.lightness() < 128


def resolve_theme(app: QApplication, mode: str) -> str:
    if mode == "system":
        return "dark" if system_is_dark(app) else "light"
    return mode


def load_qss(name: str) -> str:
    return (QSS_DIR / f"{name}.qss").read_text("utf-8")


class ThemeManager(QObject):
    """Хранит текущую тему; при смене перевешивает QSS и уведомляет виджеты."""

    theme_changed = Signal(str)   # 'light' | 'dark'

    def __init__(self) -> None:
        super().__init__()
        self.current = "light"

    def colors(self) -> dict[str, str]:
        """Токены активной темы — виды красят делегатов из них."""
        return DARK_COLORS if self.current == "dark" else LIGHT_COLORS

    def apply(self, app: QApplication, mode: str) -> None:
        resolved = resolve_theme(app, mode)
        self.current = resolved
        c = self.colors()
        # Палитра нужна для виджетов без QSS-охвата (в основном тёмный режим)
        pal = QPalette()
        bg, text = QColor(c["canvas"]), QColor(c["text"])
        pal.setColor(QPalette.ColorRole.Window, bg)
        pal.setColor(QPalette.ColorRole.Base, bg)
        pal.setColor(QPalette.ColorRole.Text, text)
        pal.setColor(QPalette.ColorRole.WindowText, text)
        pal.setColor(QPalette.ColorRole.Highlight, QColor(c["accent"]))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(c["on_accent"]))
        app.setPalette(pal)
        app.setStyleSheet(load_qss(resolved))
        self.theme_changed.emit(resolved)


_theme = ThemeManager()


def theme() -> ThemeManager:
    return _theme


def apply_theme(app: QApplication, mode: str) -> None:
    _theme.apply(app, mode)
