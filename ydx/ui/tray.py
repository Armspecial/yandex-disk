"""Иконка в области уведомлений: квота, быстрое открытие, выход."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ydx.core.models import Quota
from ydx.services import i18n
from ydx.services.fmt import human_size

log = logging.getLogger(__name__)

TRAY_GRAY = "#9A9CA3"


def _tray_icon(color: str) -> QIcon:
    """Цветной квадрат-статус. Пиксели для dpr 1 и 2 — чтобы не мылило."""
    icon = QIcon()
    for dpr in (1, 2):
        size = 22 * dpr
        pm = QPixmap(size, size)
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QColor(0, 0, 0, 0))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(2, 2, 18, 18, 5, 5)
        p.setBrush(QColor(color))
        p.drawRoundedRect(4, 4, 14, 14, 4, 4)
        p.end()
        icon.addPixmap(pm)
    return icon


class TrayController(QObject):
    def __init__(self, window, accounts: object) -> None:
        super().__init__(window)
        self._window = window
        self._accounts = accounts
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        if not self.available:
            log.warning("system tray недоступен — работаем без иконки")
            return

        self._active = False
        self.tray = QSystemTrayIcon(_tray_icon(TRAY_GRAY), self)
        self.tray.setToolTip(i18n.tr("app.title"))
        menu = QMenu()

        self.quota_action = QAction("", menu)
        self.quota_action.setEnabled(False)
        menu.addAction(self.quota_action)
        menu.addSeparator()

        act_open = QAction(i18n.tr("tray.open"), menu)
        act_open.triggered.connect(self._show_window)
        menu.addAction(act_open)
        menu.addSeparator()
        act_quit = QAction(i18n.tr("app.quit"), menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_window()
            if reason in (
                QSystemTrayIcon.ActivationReason.DoubleClick,
                QSystemTrayIcon.ActivationReason.Trigger,
            )
            else None
        )
        self.tray.show()

        accounts.quota_updated.connect(self._on_quota)

    # --- обновления ------------------------------------------------------------

    def _show_window(self) -> None:
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    @staticmethod
    def _quit() -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.quit()

    def _on_quota(self, quota: Quota) -> None:
        if not self.available:
            return
        self.quota_action.setText(
            i18n.tr("tray.quota", used=human_size(quota.used), total=human_size(quota.total))
        )
