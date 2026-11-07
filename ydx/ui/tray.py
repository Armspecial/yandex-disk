"""Иконка в области уведомлений: скорости передач, квота, открытие, выход."""

from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ydx.constants import ACCENT
from ydx.core.models import Quota
from ydx.services import i18n
from ydx.services.fmt import human_size, transfer_speeds_text

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
    def __init__(self, window, accounts: object, engine=None) -> None:
        super().__init__(window)
        self._window = window
        self._accounts = accounts
        self._engine = engine
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        if not self.available:
            log.warning("system tray недоступен — работаем без иконки")
            return

        self._active = False
        self._last_refresh = 0.0
        self.tray = QSystemTrayIcon(_tray_icon(TRAY_GRAY), self)
        self.tray.setToolTip(i18n.tr("app.title"))
        menu = QMenu()

        # суммарная скорость передач (показывается только когда есть активные)
        self.speed_action = QAction("", menu)
        self.speed_action.setEnabled(False)
        self.speed_action.setVisible(False)
        menu.addAction(self.speed_action)
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
        if engine is not None:
            engine.task_updated.connect(self._on_transfer_progress)
            engine.tasks_changed.connect(self._refresh_speeds)
            engine.task_done.connect(lambda _t: self._refresh_speeds())
            engine.task_error.connect(lambda _t: self._refresh_speeds())
        i18n.register(self.retranslate)

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

    def _on_transfer_progress(self, task_id: int) -> None:
        now = time.monotonic()
        if now - self._last_refresh < 1.0:   # трей обновляем раз в секунду
            return
        self._last_refresh = now
        self._refresh_speeds()

    def _refresh_speeds(self) -> None:
        """Скорости в меню и подсказке; иконка желтеет при передаче."""
        if not self.available or self._engine is None:
            return
        active = any(t.state == "running" for t in self._engine.tasks)
        text = transfer_speeds_text(*self._engine.speed_totals())
        if active and text:
            self.speed_action.setText(text)
            self.speed_action.setVisible(True)
            self.tray.setToolTip(f"{i18n.tr('app.title')} — {text}")
        else:
            self.speed_action.setVisible(False)
            self.tray.setToolTip(i18n.tr("app.title"))
        if active != self._active:   # иконку перерисовываем только на переходе
            self._active = active
            self.tray.setIcon(_tray_icon(ACCENT if active else TRAY_GRAY))

    def retranslate(self) -> None:
        self._refresh_speeds()
