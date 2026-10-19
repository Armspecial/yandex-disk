"""Главное окно: сайдбар + стек разделов, меню аккаунта, цикл входа.

M1: каркас с плейсхолдерами разделов; разделы наполняются в M2–M5.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QSystemTrayIcon,
    QWidget,
)

from ydx.core.models import Account, Quota
from ydx.services import i18n
from ydx.services.accounts import AVATAR_COLORS, AccountManager
from ydx.services.settings import AppSettings
from ydx.ui.dialogs.login import LoginDialog
from ydx.ui.sidebar import SECTIONS, Sidebar
from ydx.ui.widgets import icon, make_avatar

PLACEHOLDER_PAGES = ("files", "recent", "photos", "shares", "transfers", "trash")


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings, accounts: AccountManager) -> None:
        super().__init__()
        self._settings = settings
        self._accounts = accounts

        self.setObjectName("mainWindow")
        self.setWindowTitle(i18n.tr("app.title"))
        self.resize(1200, 780)

        central = QWidget()
        central.setObjectName("centralWrap")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar()
        layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self._pages: dict[str, QWidget] = {}
        for key in PLACEHOLDER_PAGES:
            page = self._make_placeholder(key)
            self._pages[key] = page
            self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)

        # --- нижний баннер синхронизации: проценты и скорость ------------------
        from PySide6.QtWidgets import QHBoxLayout as _HBox
        from PySide6.QtWidgets import QProgressBar, QPushButton as _Btn

        self.sync_banner = QWidget()
        self.sync_banner.setObjectName("syncBanner")
        banner_lay = _HBox(self.sync_banner)
        banner_lay.setContentsMargins(14, 6, 14, 6)
        banner_lay.setSpacing(10)
        self.sync_banner_label = QLabel()
        self.sync_banner_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sync_banner_label.setObjectName("dialogHint")
        self.sync_banner_bar = QProgressBar()
        self.sync_banner_bar.setTextVisible(False)
        self.sync_banner_bar.setFixedHeight(6)
        self.sync_banner_bar.setFixedWidth(220)
        self.sync_banner_sync_btn = _Btn()
        self.sync_banner_sync_btn.setProperty("class", "icon")
        self.sync_banner_sync_btn.setIcon(icon("refresh"))
        self.sync_banner_sync_btn.clicked.connect(self._sync_now)
        self.sync_banner_label.mousePressEvent = lambda _e: self.show_sync_dialog()
        banner_lay.addWidget(self.sync_banner_label, 1)
        banner_lay.addWidget(self.sync_banner_bar)
        banner_lay.addWidget(self.sync_banner_sync_btn)
        self.sync_banner.hide()
        layout.addWidget(self.sync_banner)

        self.setCentralWidget(central)

        # сервис превью (общий для разделов)
        from ydx.services.thumbs import ThumbService
        self.thumbs = ThumbService(lambda: self._accounts.api)

        # --- связи ---
        self.sidebar.section_changed.connect(self._show_section)
        self.sidebar.settings_requested.connect(self._open_settings)
        self.sidebar.account_requested.connect(self._show_account_menu)
        self.sidebar.sync_requested.connect(self.show_sync_dialog)

        # демон yandex-disk (опционален) + область уведомлений
        from ydx.services.daemon import DaemonController
        from ydx.ui.tray import TrayController

        self.daemon = DaemonController(accounts)
        self.sidebar.sync_btn.setVisible(self.daemon.installed)
        self.daemon.availability_changed.connect(self.sidebar.sync_btn.setVisible)
        if self.daemon.installed:
            self.daemon.watch()
            self.daemon.status_updated.connect(self._on_daemon_status_banner)
        self.tray = TrayController(self, accounts, self.daemon)

        accounts.accounts_changed.connect(self._on_accounts_changed)
        accounts.active_changed.connect(self._on_active_changed)
        accounts.quota_updated.connect(self.sidebar.set_quota)
        accounts.auth_failed.connect(self._on_auth_failed)

        # реальные разделы (M2 — файлы, M3 — передачи, M4 — корзина/общие/фото/недавние)
        from ydx.services.downloaded_index import DownloadedIndex
        from ydx.services.error_center import ErrorCenter
        from ydx.services.state_service import StateService
        from ydx.services.transfers import TransferEngine
        from ydx.ui.errors_ui import attach_center
        from ydx.ui.views.files_page import FilesPage
        from ydx.ui.views.photos_page import PhotosPage
        from ydx.ui.views.recent_page import RecentPage
        from ydx.ui.views.shares_page import SharesPage
        from ydx.ui.views.trash_page import TrashPage
        from ydx.ui.views.transfers_view import TransfersView

        self.error_center = ErrorCenter(self)
        attach_center(self.error_center)
        self.error_center.changed.connect(self._on_errors_changed)
        self.sidebar.errors_requested.connect(self._show_errors)
        self.sidebar.set_error_count(0)

        from ydx.services.failed_index import FailedIndex

        self.failed_index = FailedIndex(self)

        self.downloaded_index = DownloadedIndex(self)
        self.engine = TransferEngine(accounts)

        # скачанный файл изменили локально → перезалить в облако
        self.downloaded_index.file_changed.connect(self._reupload_changed)

        # завершённые скачивания → значок «доступно офлайн»
        self.engine.task_done.connect(self._on_task_done)
        self.engine.task_error.connect(
            lambda t: (
                self.error_center.report(
                    "transfers",
                    f"{t.remote_path.rsplit('/', 1)[-1]}: {t.state}"
                    + (f" ({t.error})" if t.error else ""),
                ),
                self.failed_index.mark(t.remote_path, t.error or t.state),
            )
        )
        # повтор удался — ✖ с файла снимается
        self.engine.task_done.connect(
            lambda t: self.failed_index.discard(t.remote_path)
        )
        self.failed_index.changed.connect(
            lambda: self.files_page._sync_refresh_timer.start()
        )

        self.state_service = StateService(self.downloaded_index, self.failed_index, self)

        self.files_page = FilesPage(
            settings, accounts, self.thumbs, self.engine,
            self.downloaded_index, self.failed_index,
        )
        self.replace_page("files", self.files_page)
        self.transfers_view = TransfersView(self.engine)
        self.replace_page("transfers", self.transfers_view)
        self.recent_page = RecentPage(accounts, self.thumbs, self.state_service)
        self.recent_page.show_in_folder_requested.connect(self._show_in_folder)
        self.replace_page("recent", self.recent_page)
        self.photos_page = PhotosPage(accounts, self.thumbs, self.state_service)
        self.replace_page("photos", self.photos_page)
        self.shares_page = SharesPage(accounts, self.thumbs, self.state_service)
        self.replace_page("shares", self.shares_page)
        self.trash_page = TrashPage(accounts, self.thumbs)
        self.replace_page("trash", self.trash_page)

        self.sidebar.set_secure(True)   # заменяется реальным флагом в app.py
        self._token_secure = True
        self._tray_toast_shown = False
        self.sidebar.set_section("files")
        i18n.register(self.retranslate)
        self.retranslate()

        # Ctrl+W — закрыть окно (в трей), как кнопкой «✕»
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self.close)

    def _on_daemon_status_banner(self, status) -> None:
        """Нижний баннер: проценты и скорость, пока идёт синхронизация."""
        active = status.state in ("busy", "index")
        self.sync_banner.setVisible(active)
        if not active:
            return
        from ydx.core.daemon_status import sync_percent, sync_speed

        text = i18n.tr(f"sync.state.{status.state}")
        pct = sync_percent(status.progress)
        speed = sync_speed(status.progress)
        if status.state == "error" and status.progress:
            self.error_center.report("sync", status.progress)
        if pct is not None:
            text += f" {pct:.0f}%"
        if speed:
            text += f" · {speed}"
        self.sync_banner_label.setText(text)
        if pct is None:
            self.sync_banner_bar.setRange(0, 0)      # неопределённый прогресс
            self.sync_banner_bar.setValue(0)
        else:
            self.sync_banner_bar.setRange(0, 100)
            self.sync_banner_bar.setValue(int(pct))

    def _sync_now(self) -> None:
        if self.daemon.installed:
            self.daemon.sync_now()

    def _on_task_done(self, task) -> None:
        """Скачали файл → индекс офлайн-копий + значок + уведомление."""
        if task.kind == "download":
            self.downloaded_index.add(task.remote_path, task.local_path)
            if getattr(self.tray, "available", False):
                self.tray.tray.showMessage(
                    i18n.tr("app.title"),
                    i18n.tr("files.downloaded_to",
                            name=task.remote_path.rsplit("/", 1)[-1],
                            folder=task.local_path.rsplit("/", 1)[0]),
                    QSystemTrayIcon.MessageIcon.Information,
                )

    def _reupload_changed(self, remote_path: str, local_path: str) -> None:
        """Локально изменили скачанный файл — обновляем копию в облаке."""
        remote_dir = remote_path.rsplit("/", 1)[0] or "disk:/"
        self.engine.enqueue_upload([local_path], remote_dir, overwrite=True)

    def _on_errors_changed(self) -> None:
        self.sidebar.set_error_count(self.error_center.count)
        if self.error_center.count == 0:
            self.failed_index.clear()   # список ошибок очистили — ✖ тоже уходит

    def _show_errors(self) -> None:
        from ydx.ui.dialogs.errors_dlg import ErrorsDialog

        dlg = ErrorsDialog(self.error_center, self)
        dlg.exec()
        dlg.deleteLater()

    # --- страницы (плейсхолдеры M1) ------------------------------------------------

    def _make_placeholder(self, key: str) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        label = QLabel(i18n.tr("page.placeholder"))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(label)
        page.setProperty("placeholder_label", label)
        return page

    def page(self, key: str) -> QWidget:
        return self._pages[key]

    def replace_page(self, key: str, widget: QWidget) -> None:
        """Заменить плейсхолдер реальным разделом (используется в M2+)."""
        idx = self.stack.indexOf(self._pages[key])
        self.stack.removeWidget(self._pages[key])
        self._pages[key].deleteLater()
        self._pages[key] = widget
        self.stack.insertWidget(idx, widget)

    def _show_section(self, key: str) -> None:
        self.stack.setCurrentWidget(self._pages[key])
        page = self._pages[key]
        if hasattr(page, "on_shown"):
            page.on_shown()

    def _show_in_folder(self, path: str) -> None:
        """Из «Недавних»: открыть папку файла в разделе «Файлы»."""
        self.sidebar.set_section("files")
        self._show_section("files")
        self.files_page.load_path(path, remember=False)

    # --- аккаунты ----------------------------------------------------------------------

    def _on_accounts_changed(self, accounts: list[Account]) -> None:
        pass   # список обновляется при открытии меню

    def _on_active_changed(self, account: Account | None) -> None:
        self.sidebar.set_account(account)
        if account is not None:
            self._accounts.refresh_quota()

    def _show_account_menu(self, global_pos) -> None:
        menu = QMenu(self)
        for account in self._accounts.accounts:
            action = QAction(
                make_avatar(account.title, AVATAR_COLORS[account.color_index % len(AVATAR_COLORS)], 20),
                account.title,
                menu,
            )
            action.setCheckable(True)
            action.setChecked(account.login == self._accounts.active.login if self._accounts.active else False)
            action.triggered.connect(lambda _=False, a=account: self._accounts.switch_to(a.login))
            menu.addAction(action)
        menu.addSeparator()
        add_action = QAction(i18n.tr("account.add"), menu)
        add_action.triggered.connect(self.show_login)
        menu.addAction(add_action)
        if self._accounts.active is not None:
            menu.addSeparator()
            logout_action = QAction(i18n.tr("account.logout"), menu)
            logout_action.triggered.connect(self._logout_active)
            menu.addAction(logout_action)
        menu.exec(global_pos)

    def _logout_active(self) -> None:
        self._accounts.logout()
        if not self._accounts.accounts:
            self.sidebar.set_quota(None)

    def _on_auth_failed(self, account: Account | None) -> None:
        # токен отозван/не обновился — выходим из аккаунта и предлагаем вход
        if account is not None:
            self._accounts.logout(account.login)
        QTimer.singleShot(0, self.show_login)

    # --- вход/настройки -------------------------------------------------------------------

    def show_login(self) -> None:
        dialog = LoginDialog(self._settings, self)
        if dialog.exec() and dialog.result_data:
            bundle, _login, display_name, avatar_url = dialog.result_data
            self._accounts.add_account(bundle, display_name, avatar_url=avatar_url)

    def _open_settings(self) -> None:
        from PySide6.QtWidgets import QApplication

        from ydx.ui.dialogs.settings_dlg import SettingsDialog

        dlg = SettingsDialog(self._settings, QApplication.instance(), self,
                             daemon=self.daemon)
        dlg.set_secure(self._token_secure)
        dlg.exec()

    def show_sync_dialog(self) -> None:
        from ydx.ui.panels.sync_panel import SyncDialog

        if self.daemon.installed:
            dlg = SyncDialog(self.daemon, self._accounts.resources, self)
            dlg.exec()
            # разорвать связь с 2-секундными обновлениями статуса + убрать диалог
            try:
                self.daemon.status_updated.disconnect(dlg._on_status)
            except (RuntimeError, TypeError):
                pass
            dlg.deleteLater()

    # --- прочее ------------------------------------------------------------------------------

    def retranslate(self) -> None:
        self.setWindowTitle(i18n.tr("app.title"))
        for key in PLACEHOLDER_PAGES:
            page = self._pages[key]
            label = page.property("placeholder_label")
            if isinstance(label, QLabel):
                label.setText(i18n.tr("page.placeholder"))
        current = self._current_section()
        if current:
            self.sidebar.set_section(current)

    def _current_section(self) -> str | None:
        for key, page in self._pages.items():
            if self.stack.currentWidget() is page:
                return key
        return None

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
        """«✕» сворачивает в трей (полный выход — через меню иконки).

        Без области уведомлений закрываемся по-настоящему.
        """
        tray_ok = hasattr(self, "tray") and getattr(self.tray, "available", False)
        if tray_ok and not getattr(self, "_force_quit", False):
            event.ignore()
            self.hide()
            if not self._tray_toast_shown:
                self._tray_toast_shown = True
                self.tray.tray.showMessage(
                    i18n.tr("app.title"),
                    i18n.tr("tray.hidden_toast"),
                    QSystemTrayIcon.MessageIcon.Information,
                )
            return
        if hasattr(self, "daemon"):
            self.daemon.shutdown()
        super().closeEvent(event)
