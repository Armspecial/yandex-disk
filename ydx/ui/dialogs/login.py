"""Диалог входа: код устройства (primary), браузер, ручная вставка токена.

Диалог валидирует токен запросом к API и возвращает
(bundle, login, display_name) в атрибуте result_data.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ydx.core.api_client import ApiClient, StaticTokenProvider
from ydx.core.api_resources import ResourcesApi
from ydx.core.errors import NetworkError
from ydx.core.models import TokenBundle
from ydx.core.oauth import OAuthClient, RedirectListener, new_state
from ydx.services import i18n
from ydx.services.settings import AppSettings
from ydx.services.workers import run_in_thread

PAGE_CONFIG, PAGE_METHODS, PAGE_DEVICE, PAGE_BROWSER, PAGE_PASTE, PAGE_BUSY = range(6)


def _open_url(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))


class _MethodRow(QFrame):
    """Кликабельная строка «заголовок + подсказка» на странице способов входа."""

    def __init__(self, title: str, hint: str, on_click: Callable[[], None]) -> None:
        super().__init__()
        self.setObjectName("methodRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._on_click = on_click
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        texts = QWidget(self)
        col = QVBoxLayout(texts)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("methodTitle")
        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("methodHint")
        self.hint_label.setWordWrap(True)
        col.addWidget(self.title_label)
        col.addWidget(self.hint_label)
        lay.addWidget(texts, 1)
        lay.addWidget(QLabel("›"))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mouseReleaseEvent(event)

    def set_texts(self, title: str, hint: str) -> None:
        self.title_label.setText(title)
        self.hint_label.setText(hint)


class LoginDialog(QDialog):
    def __init__(self, settings: AppSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._cancelled = False
        self._listener: RedirectListener | None = None
        self._device_info = None
        self.result_data: tuple[TokenBundle, str | None, str] | None = None

        self.setModal(True)
        self.setFixedSize(540, 480)
        self.setWindowTitle(i18n.tr("login.welcome"))

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 26, 28, 24)
        root.setSpacing(12)

        self.title = QLabel()
        self.title.setObjectName("dialogTitle")
        root.addWidget(self.title)
        self.hint = QLabel()
        self.hint.setObjectName("dialogHint")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorBanner")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        self._build_pages()
        self.retranslate()
        i18n.register(self.retranslate)
        QTimer.singleShot(0, self._initial_page)

    # --- построение страниц -----------------------------------------------------

    def _build_pages(self) -> None:
        self.stack.addWidget(self._page_config())
        self.stack.addWidget(self._page_methods())
        self.stack.addWidget(self._page_device())
        self.stack.addWidget(self._page_browser())
        self.stack.addWidget(self._page_paste())
        self.stack.addWidget(self._page_busy())

    def _page_config(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 8, 0, 0)
        col.setSpacing(10)
        self.cfg_id_edit = QLineEdit()
        self.cfg_id_edit.setText(self._settings.client_id)
        col.addWidget(self._labeled(self.cfg_id_edit, i18n.tr("login.client_id")))
        self.cfg_secret_edit = QLineEdit()
        self.cfg_secret_edit.setText(self._settings.client_secret)
        col.addWidget(self._labeled(self.cfg_secret_edit, i18n.tr("login.client_secret")))
        self.cfg_oauth_link = QPushButton(i18n.tr("login.open_oauth"))
        self.cfg_oauth_link.setProperty("class", "flat")
        self.cfg_oauth_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cfg_oauth_link.clicked.connect(
            lambda: _open_url("https://oauth.yandex.ru/client/new/")
        )
        col.addWidget(self.cfg_oauth_link)
        col.addStretch(1)
        row = QHBoxLayout()
        self.cfg_paste_btn = QPushButton(i18n.tr("login.method_paste"))
        self.cfg_paste_btn.clicked.connect(lambda: self._go(PAGE_PASTE))
        self.cfg_continue_btn = QPushButton(i18n.tr("login.continue"))
        self.cfg_continue_btn.setProperty("class", "primary")
        self.cfg_continue_btn.clicked.connect(self._save_config_and_continue)
        row.addWidget(self.cfg_paste_btn)
        row.addStretch(1)
        row.addWidget(self.cfg_continue_btn)
        col.addLayout(row)
        return page

    def _page_methods(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 8, 0, 0)
        col.setSpacing(10)
        self.method_rows = [
            _MethodRow("", "", lambda t=PAGE_DEVICE: self._go(t)),
            _MethodRow("", "", lambda t=PAGE_BROWSER: self._go(t)),
            _MethodRow("", "", lambda t=PAGE_PASTE: self._go(t)),
        ]
        for row in self.method_rows:
            col.addWidget(row)
        col.addStretch(1)
        return page

    def _page_device(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 8, 0, 0)
        col.setSpacing(14)
        col.addStretch(1)
        self.device_code_label = QLabel("…")
        self.device_code_label.setObjectName("deviceCode")
        self.device_code_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.device_code_label)
        self.device_open_btn = QPushButton(i18n.tr("login.device_open"))
        self.device_open_btn.setProperty("class", "primary")
        self.device_open_btn.clicked.connect(self._open_device_url)
        col.addWidget(self.device_open_btn, 0, Qt.AlignmentFlag.AlignCenter)
        self.device_waiting_label = QLabel(i18n.tr("login.device_waiting"))
        self.device_waiting_label.setObjectName("dialogHint")
        self.device_waiting_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.device_waiting_label)
        col.addStretch(1)
        self.device_back_btn = QPushButton(i18n.tr("login.back"))
        self.device_back_btn.clicked.connect(lambda: self._go(PAGE_METHODS))
        col.addWidget(self.device_back_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return page

    def _page_browser(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 8, 0, 0)
        col.setSpacing(14)
        col.addStretch(1)
        self.browser_open_btn = QPushButton(i18n.tr("login.browser_open"))
        self.browser_open_btn.setProperty("class", "primary")
        self.browser_open_btn.clicked.connect(self._start_browser_flow)
        col.addWidget(self.browser_open_btn, 0, Qt.AlignmentFlag.AlignCenter)
        self.browser_waiting_label = QLabel(i18n.tr("login.browser_waiting"))
        self.browser_waiting_label.setObjectName("dialogHint")
        self.browser_waiting_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.browser_waiting_label)
        col.addStretch(1)
        self.browser_back_btn = QPushButton(i18n.tr("login.back"))
        self.browser_back_btn.clicked.connect(lambda: self._go(PAGE_METHODS))
        col.addWidget(self.browser_back_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return page

    def _page_paste(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 8, 0, 0)
        col.setSpacing(10)
        self.paste_edit = QLineEdit()
        self.paste_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.paste_edit.setPlaceholderText(i18n.tr("login.paste_placeholder"))
        col.addWidget(self._labeled(self.paste_edit, i18n.tr("login.paste_hint")))
        col.addStretch(1)
        row = QHBoxLayout()
        self.paste_back_btn = QPushButton(i18n.tr("login.back"))
        self.paste_back_btn.clicked.connect(
            lambda: self._go(PAGE_METHODS if self._settings.client_id else PAGE_CONFIG)
        )
        self.paste_ok_btn = QPushButton(i18n.tr("common.ok"))
        self.paste_ok_btn.setProperty("class", "primary")
        self.paste_ok_btn.clicked.connect(self._use_pasted_token)
        row.addWidget(self.paste_back_btn)
        row.addStretch(1)
        row.addWidget(self.paste_ok_btn)
        col.addLayout(row)
        return page

    def _page_busy(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.addStretch(1)
        self.busy_label = QLabel(i18n.tr("login.verifying"))
        self.busy_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.busy_label)
        col.addStretch(1)
        self.busy_cancel_btn = QPushButton(i18n.tr("login.cancel"))
        self.busy_cancel_btn.clicked.connect(self.reject)
        col.addWidget(self.busy_cancel_btn, 0, Qt.AlignmentFlag.AlignCenter)
        return page

    def _labeled(self, editor: QLineEdit, caption: str) -> QWidget:
        holder = QFrame()
        v = QVBoxLayout(holder)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        lab = QLabel(caption)
        lab.setObjectName("dialogHint")
        v.addWidget(lab)
        v.addWidget(editor)
        return holder

    # --- навигация и действия -------------------------------------------------------

    def _initial_page(self) -> None:
        self._go(PAGE_METHODS if self._settings.client_id else PAGE_CONFIG)

    def _go(self, page: int) -> None:
        self.error_label.hide()
        self.stack.setCurrentIndex(page)
        if page == PAGE_DEVICE:
            self._start_device_flow()

    def _save_config_and_continue(self) -> None:
        client_id = self.cfg_id_edit.text().strip()
        if not client_id:
            self._show_error(i18n.tr("login.client_id") + " — ?")
            return
        self._settings.set("client_id", client_id)
        self._settings.set("client_secret", self.cfg_secret_edit.text().strip())
        self._go(PAGE_METHODS)

    def _oauth(self) -> OAuthClient:
        return OAuthClient(self._settings.client_id, self._settings.client_secret)

    def _start_device_flow(self) -> None:
        self.device_code_label.setText("…")
        self._cancelled = False
        run_in_thread(
            lambda: self._oauth().start_device_code("yandex-disk-linux", "Linux PC"),
            self._device_started,
            self._show_error_exc,
        )

    def _device_started(self, info) -> None:
        if self._cancelled:
            return
        self._device_info = info
        self.device_code_label.setText(info.user_code)
        _open_url(info.verification_url)
        run_in_thread(
            lambda: self._oauth().poll_device_code(
                info, should_cancel=lambda: self._cancelled
            ),
            self._verify_bundle,
            self._show_error_exc,
        )

    def _open_device_url(self) -> None:
        if self._device_info is not None:
            _open_url(self._device_info.verification_url)

    def _start_browser_flow(self) -> None:
        self._cancelled = False
        state = new_state()
        listener = RedirectListener()
        try:
            listener.start()
        except OSError as exc:
            self._show_error(f"localhost:{listener.port}: {exc}")
            return
        self._listener = listener
        _open_url(self._oauth().authorize_url(state))
        self._go(PAGE_BUSY)

        def job() -> TokenBundle:
            code = listener.wait_code(600, state)
            return self._oauth().exchange_code(code)

        run_in_thread(
            job,
            lambda bundle: (listener.stop(), self._verify_bundle(bundle)),
            lambda exc: (listener.stop(), self._show_error_exc(exc)),
        )

    def _use_pasted_token(self) -> None:
        token = self.paste_edit.text().strip()
        if token:
            self._verify_bundle(TokenBundle(access_token=token))

    def _verify_bundle(self, bundle: TokenBundle) -> None:
        self._go(PAGE_BUSY)

        def job():
            client = ApiClient(StaticTokenProvider(bundle.access_token))
            ResourcesApi(client).disk_info()      # 401/403 → AuthError
            return OAuthClient.fetch_login(bundle.access_token)

        def done(data):
            login, display_name = data
            self.result_data = (bundle, login, display_name or "")
            self.accept()

        run_in_thread(job, done, self._show_error_exc)

    # --- ошибки и переводы -------------------------------------------------------------

    def _show_error_exc(self, exc: Exception) -> None:
        key = "login.err_network" if isinstance(exc, NetworkError) else "login.err_auth"
        self._show_error(i18n.tr(key, err=exc))

    def _show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.show()
        if self.stack.currentIndex() == PAGE_BUSY:
            self.stack.setCurrentIndex(PAGE_METHODS)

    def retranslate(self) -> None:
        self.setWindowTitle(i18n.tr("login.welcome"))
        self.title.setText(i18n.tr("login.welcome"))
        self.hint.setText(i18n.tr("login.client_hint"))
        self.cfg_oauth_link.setText(i18n.tr("login.open_oauth"))
        self.cfg_paste_btn.setText(i18n.tr("login.method_paste"))
        self.cfg_continue_btn.setText(i18n.tr("login.continue"))
        for row, (title_key, hint_key) in zip(
            self.method_rows,
            [
                ("login.method_device", "login.method_device_hint"),
                ("login.method_browser", "login.method_browser_hint"),
                ("login.method_paste", "login.method_paste_hint"),
            ],
        ):
            row.set_texts(i18n.tr(title_key), i18n.tr(hint_key))
        self.device_open_btn.setText(i18n.tr("login.device_open"))
        self.device_waiting_label.setText(i18n.tr("login.device_waiting"))
        self.device_back_btn.setText(i18n.tr("login.back"))
        self.browser_open_btn.setText(i18n.tr("login.browser_open"))
        self.browser_waiting_label.setText(i18n.tr("login.browser_waiting"))
        self.browser_back_btn.setText(i18n.tr("login.back"))
        self.paste_edit.setPlaceholderText(i18n.tr("login.paste_placeholder"))
        self.paste_back_btn.setText(i18n.tr("login.back"))
        self.paste_ok_btn.setText(i18n.tr("common.ok"))
        self.busy_label.setText(i18n.tr("login.verifying"))
        self.busy_cancel_btn.setText(i18n.tr("login.cancel"))

    def reject(self) -> None:  # noqa: N802 — Qt API
        self._cancelled = True
        if self._listener is not None:
            self._listener.stop()
        super().reject()

    def done(self, result: int) -> None:  # noqa: N802 — Qt API
        i18n.unregister(self.retranslate)   # диалог закрыт — колбэк не нужен
        super().done(result)
