"""Применение темы (светлая/тёмная/системная) через QSS."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

QSS_DIR = Path(__file__).parent.parent / "resources" / "qss"


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

    def apply(self, app: QApplication, mode: str) -> None:
        resolved = resolve_theme(app, mode)
        self.current = resolved
        # Палитра нужна для виджетов без QSS-охвата (в основном тёмный режим)
        pal = QPalette()
        if resolved == "dark":
            bg, text = QColor("#1E1E21"), QColor("#F2F2F4")
        else:
            bg, text = QColor("#FFFFFF"), QColor("#21201F")
        pal.setColor(QPalette.ColorRole.Window, bg)
        pal.setColor(QPalette.ColorRole.Base, bg)
        pal.setColor(QPalette.ColorRole.Text, text)
        pal.setColor(QPalette.ColorRole.WindowText, text)
        app.setPalette(pal)
        app.setStyleSheet(load_qss(resolved))
        self.theme_changed.emit(resolved)


_theme = ThemeManager()


def theme() -> ThemeManager:
    return _theme


def apply_theme(app: QApplication, mode: str) -> None:
    _theme.apply(app, mode)
