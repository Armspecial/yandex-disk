"""Сборка QApplication и запуск главного окна."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QtMsgType, qInstallMessageHandler
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from ydx import full_version
from ydx.constants import APP_ID, APP_TITLE, CACHE_DIR, CONFIG_DIR

ICON_PATH = Path(__file__).parent / "resources" / "icons" / "app.svg"

_QT_LEVEL = {
    QtMsgType.QtDebugMsg: logging.DEBUG,
    QtMsgType.QtInfoMsg: logging.INFO,
    QtMsgType.QtWarningMsg: logging.WARNING,
    QtMsgType.QtCriticalMsg: logging.ERROR,
    QtMsgType.QtFatalMsg: logging.CRITICAL,
}


def setup_logging() -> None:
    """Лог в ~/.cache/yandex-disk-linux/app.log (1 МБ × 3) + Qt-сообщения.

    Приложение живёт в трее без консоли — без файла диагностировать нечем.
    Повторный вызов ничего не добавляет (охрана для тестов).
    """
    root = logging.getLogger()
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        CACHE_DIR / "app.log", maxBytes=1 << 20, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    def qt_message(mode, context, message):
        logging.getLogger("qt").log(_QT_LEVEL.get(mode, logging.INFO), message)

    qInstallMessageHandler(qt_message)


def main(argv: list[str] | None = None) -> int:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    setup_logging()

    app = QApplication(sys.argv if argv is None else [APP_ID, *argv])
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_TITLE)
    app.setOrganizationName(APP_ID)
    app.setApplicationVersion(full_version())
    app.setDesktopFileName(APP_ID)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    # Сервисы и окно импортируются здесь — после создания QApplication.
    from ydx.services import i18n
    from ydx.services.accounts import AccountManager
    from ydx.services.settings import AppSettings
    from ydx.services.token_store import TokenStore
    from ydx.ui.main_window import MainWindow
    from ydx.ui.theme import apply_theme

    settings = AppSettings()
    i18n.load(settings.language)
    apply_theme(app, settings.theme)

    # --- single instance: второй запуск поднимает существующее окно -------------
    from PySide6.QtNetwork import QLocalServer, QLocalSocket

    probe = QLocalSocket()
    probe.connectToServer(APP_ID)
    if probe.waitForConnected(200):
        probe.write(b"show")
        probe.flush()
        probe.disconnectFromServer()
        return 0
    QLocalServer.removeServer(APP_ID)
    server = QLocalServer()
    server.listen(APP_ID)

    store = TokenStore()
    accounts = AccountManager(settings, store)
    accounts.start()

    win = MainWindow(settings, accounts)
    win.sidebar.set_secure(store.secure)
    win._token_secure = store.secure
    win.show()

    if accounts.active is None:
        from PySide6.QtCore import QTimer

        QTimer.singleShot(100, win.show_login)

    def _raise_existing() -> None:
        conn = server.nextPendingConnection()
        if conn is not None:
            conn.readyRead.connect(
                lambda: (conn.readAll(), win.show(), win.raise_(), win.activateWindow())
            )

    server.newConnection.connect(_raise_existing)

    return app.exec()
