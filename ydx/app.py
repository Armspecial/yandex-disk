"""Сборка QApplication и запуск главного окна."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ydx import __version__
from ydx.constants import APP_ID, APP_TITLE, CONFIG_DIR

ICON_PATH = Path(__file__).parent / "resources" / "icons" / "app.svg"


def main(argv: list[str] | None = None) -> int:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv if argv is None else [APP_ID, *argv])
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_TITLE)
    app.setOrganizationName(APP_ID)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(APP_ID)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    # Импорт внутри main: модули UI тянут ресурсы, зависящие от QApplication.
    from ydx.ui.main_window import MainWindow

    win = MainWindow()
    win.show()
    return app.exec()
