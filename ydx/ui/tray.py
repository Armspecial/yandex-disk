"""Иконка в области уведомлений: статус демона, быстрое управление."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ydx.constants import ACCENT, DANGER, SUCCESS
from ydx.core.models import DaemonStatus, Quota
from ydx.services import i18n
from ydx.services.daemon import DaemonController
from ydx.services.fmt import human_size

log = logging.getLogger(__name__)


def _status_icon(state: str) -> QIcon:
    """Цветной квадрат-статус: зелёный idle, жёлтый busy, красный error,
    серый stopped/none. Пиксели для dpr 1 и 2 — чтобы не мылило."""
    colors = {
        "idle": SUCCESS,
        "busy": ACCENT,
        "index": ACCENT,
        "paused": "#B8860B",
        "error": DANGER,
        "no-internet": DANGER,
        "paywall": DANGER,
        "stopped": "#9A9CA3",
    }
    color = QColor(colors.get(state, "#9A9CA3"))
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
        p.setBrush(color)
        p.drawRoundedRect(4, 4, 14, 14, 4, 4)
        p.end()
        icon.addPixmap(pm)
    return icon


class TrayController(QObject):
    def __init__(self, window, accounts: object, daemon: DaemonController) -> None:
        super().__init__(window)
        self._window = window
        self._accounts = accounts
        self._daemon = daemon
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        if not self.available:
            log.warning("system tray недоступен — работаем без иконки")
            return

        self.tray = QSystemTrayIcon(_status_icon("stopped"), self)
        self.tray.setToolTip(i18n.tr("app.title"))
        menu = QMenu()
        self._last_state = "stopped"

        self.status_action = QAction(i18n.tr("sync.state.stopped"), menu)
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)
        self.quota_action = QAction("", menu)
        self.quota_action.setEnabled(False)
        menu.addAction(self.quota_action)
        menu.addSeparator()

        act_open = QAction(i18n.tr("tray.open"), menu)
        act_open.triggered.connect(self._show_window)
        menu.addAction(act_open)

        # действия демона — только если бинарь установлен
        self.sync_actions: list[QAction] = []
        if daemon.installed:
            act_sync = QAction(i18n.tr("sync.title") + "…", menu)
            act_sync.triggered.connect(self._window.show_sync_dialog)
            menu.addAction(act_sync)
            self.pause_action = QAction(i18n.tr("sync.pause"), menu)
            self.pause_action.triggered.connect(lambda: self._daemon.pause_for(2))
            menu.addAction(self.pause_action)
            act_stop = QAction(i18n.tr("sync.stop"), menu)
            act_stop.triggered.connect(self._daemon.stop)
            menu.addAction(act_stop)
            self.sync_actions.extend((act_sync, self.pause_action, act_stop))
        menu.addSeparator()
        act_quit = QAction(i18n.tr("app.quit"), menu)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_window()
            if reason == QSystemTrayIcon.ActivationReason.DoubleClick
            else None
        )
        self.tray.show()

        accounts.quota_updated.connect(self._on_quota)
        daemon.status_updated.connect(self._on_status)

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

    def _on_status(self, status: DaemonStatus) -> None:
        if not self.available:
            return
        key = f"sync.state.{status.state}"
        text = i18n.tr(key)
        if text == key:
            text = status.state
        self.status_action.setText(text)
        self.tray.setToolTip(f"{i18n.tr('app.title')} — {text}")
        self.tray.setIcon(_status_icon(status.state))
        # уведомление только при ПЕРЕХОДЕ в ошибку, не на каждом опросе
        if status.state == "error" and self._last_state != "error":
            self.tray.showMessage(
                i18n.tr("app.title"),
                status.progress or text,
                QSystemTrayIcon.MessageIcon.Warning,
            )
        self._last_state = status.state
