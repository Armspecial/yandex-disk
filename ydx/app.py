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
