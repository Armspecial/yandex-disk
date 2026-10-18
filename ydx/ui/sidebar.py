"""Левый сайдбар: аккаунт, разделы, индикатор места, настройки."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ydx.core.models import Account, Quota
from ydx.services import i18n
from ydx.services.accounts import AVATAR_COLORS
from ydx.services.fmt import human_size
from ydx.ui.widgets import ElidedLabel, icon, make_avatar

SECTIONS = [
    ("files", "nav/folder", "nav.files"),
    ("recent", "nav/clock", "nav.recent"),
    ("photos", "nav/image", "nav.photos"),
    ("shares", "nav/link", "nav.shares"),
    ("transfers", "nav/transfer", "nav.transfers"),
    ("trash", "nav/trash", "nav.trash"),
]


class Sidebar(QFrame):
    section_changed = Signal(str)
    account_requested = Signal(QPoint)   # глобальная позиция — открыть меню аккаунта
    settings_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(232)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 14, 12, 12)
        root.setSpacing(2)

        # --- аккаунт ---
        self.account_btn = QPushButton()
        self.account_btn.setObjectName("accountBtn")
        self.account_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.account_btn.setToolTip(i18n.tr("account.manage"))
        acc_layout = QHBoxLayout(self.account_btn)
        acc_layout.setContentsMargins(8, 8, 10, 8)
        acc_layout.setSpacing(10)
        self.avatar_label = QLabel()
        self.avatar_label.setFixedSize(36, 36)
        holder = QWidget(self.account_btn)
        name_col = QVBoxLayout(holder)
        name_col.setContentsMargins(0, 0, 0, 0)
        name_col.setSpacing(0)
        self.account_name = ElidedLabel()
        self.account_name.setObjectName("accountName")
        self.account_login = ElidedLabel()
        self.account_login.setObjectName("quotaLabel")
        name_col.addWidget(self.account_name)
        name_col.addWidget(self.account_login)
        acc_layout.addWidget(self.avatar_label)
        acc_layout.addWidget(holder, 1)
        root.addWidget(self.account_btn)

        root.addSpacing(10)

        # --- разделы ---
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key, ic, text_key in SECTIONS:
            btn = QPushButton()
            btn.setObjectName("navBtn")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIcon(icon(ic))
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _=False, k=key: self.section_changed.emit(k))
            self._group.addButton(btn)
            self._buttons[key] = btn
            root.addWidget(btn)
            btn.setProperty("section", key)

        root.addStretch(1)

        # --- место на диске ---
        self.quota_bar = QProgressBar()
        self.quota_bar.setObjectName("quotaBar")
        self.quota_bar.setTextVisible(False)
        self.quota_bar.setRange(0, 100)
        self.quota_bar.setValue(0)
        root.addWidget(self.quota_bar)
        self.quota_label = ElidedLabel()
        self.quota_label.setObjectName("quotaLabel")
        self.quota_free = QLabel()
        self.quota_free.setObjectName("quotaFree")
        root.addWidget(self.quota_label)
        root.addWidget(self.quota_free)

        self.security_warning = QLabel()
        self.security_warning.setObjectName("securityWarning")
        self.security_warning.setWordWrap(True)
        self.security_warning.hide()
        root.addWidget(self.security_warning)

        root.addSpacing(6)

        # --- настройки ---
        self.settings_btn = QPushButton()
        self.settings_btn.setObjectName("navBtn")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.setIcon(icon("nav/gear"))
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        root.addWidget(self.settings_btn)

        self.account_btn.clicked.connect(self._emit_account_menu)
        i18n.register(self.retranslate)
        self.retranslate()

    # --- публичное ---------------------------------------------------------------

    def set_account(self, account: Account | None) -> None:
        if account is None:
            self.avatar_label.setPixmap(make_avatar("?", AVATAR_COLORS[0]))
            self.account_name.setText(i18n.tr("account.add"))
            self.account_login.setText("")
            return
        self.avatar_label.setPixmap(make_avatar(
            account.title, AVATAR_COLORS[account.color_index % len(AVATAR_COLORS)]
        ))
        self.account_name.setText(account.title)
        self.account_login.setText(account.login if account.login else "")

    def set_quota(self, quota: Quota | None) -> None:
        if quota is None or quota.total <= 0:
            self.quota_label.setText(i18n.tr("quota.empty"))
            self.quota_free.setText("")
            self.quota_bar.setValue(0)
            return
        percent = round(quota.used * 100 / quota.total)
        self.quota_bar.setValue(min(percent, 100))
        if percent >= 95:
            self.quota_bar.setProperty("class", "danger")
        else:
            self.quota_bar.setProperty("class", "")
        self.quota_bar.style().unpolish(self.quota_bar)
        self.quota_bar.style().polish(self.quota_bar)
        self.quota_label.setText(
            i18n.tr("quota.label", used=human_size(quota.used), total=human_size(quota.total))
        )
        self.quota_free.setText(i18n.tr("quota.free", free=human_size(quota.free)))

    def set_secure(self, secure: bool) -> None:
        self.security_warning.setVisible(not secure)
        if not secure:
            self.security_warning.setText(i18n.tr("security.tokens_insecure"))

    def set_section(self, key: str) -> None:
        btn = self._buttons.get(key)
        if btn is not None:
            btn.setChecked(True)

    def retranslate(self) -> None:
        for key, _ic, text_key in SECTIONS:
            self._buttons[key].setText(i18n.tr(text_key))
        self.settings_btn.setText(i18n.tr("nav.settings"))
        if not self.account_name.text():
            self.account_name.setText(i18n.tr("account.add"))

    # --- внутреннее ----------------------------------------------------------------

    def _emit_account_menu(self) -> None:
        self.account_requested.emit(self.account_btn.mapToGlobal(
            self.account_btn.rect().bottomLeft()
        ))
