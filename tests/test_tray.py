"""TrayController: скорости передач в меню/подсказке, цвет иконки."""

from __future__ import annotations

import os

import pytest


@pytest.fixture()
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _StubEngine:
    """Движок-заглушка: те же сигналы и интерфейс, что у TransferEngine."""

    def __init__(self, tasks) -> None:
        from PySide6.QtCore import QObject, Signal

        # динамически создаём QObject с нужными сигналами
        class _Engine(QObject):
            task_updated = Signal(int)
            tasks_changed = Signal()
            task_done = Signal(object)
            task_error = Signal(object)

        self._obj = _Engine()
        self.tasks = tasks
        self.speed_totals_result = (0.0, 0.0)

    def __getattr__(self, name):
        return getattr(self._obj, name)

    def speed_totals(self):
        return self.speed_totals_result


class _StubAccounts:
    class _Sig:
        def connect(self, _slot):
            pass

    quota_updated = _Sig()


class _Win:
    """Родитель трея — обычный QWidget (QObject-родитель обязателен)."""

    def __new__(cls):
        from PySide6.QtWidgets import QWidget

        return QWidget()


def _controller(qapp, engine, monkeypatch=None):
    from PySide6.QtWidgets import QSystemTrayIcon

    from ydx.services import i18n
    from ydx.ui.tray import TrayController

    if monkeypatch is not None:
        # offscreen: области уведомлений нет, но вся логика обновления
        # (меню/подсказка/иконка) работает и без реального трея
        monkeypatch.setattr(
            QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True)
        )
    i18n.load("ru")
    tray = TrayController(_Win(), _StubAccounts(), engine)
    return tray


def test_speeds_visible_while_active(qapp, monkeypatch):
    from ydx.core.models import TransferTask

    engine = _StubEngine([
        TransferTask(id=1, kind="download", state="running"),
    ])
    engine.speed_totals_result = (1024.0, 0.0)
    tray = _controller(qapp, engine, monkeypatch)
    tray._refresh_speeds()
    assert tray.speed_action.isVisible()
    assert "↓" in tray.speed_action.text()
    assert "1 КБ/с" in tray.tray.toolTip()
    assert tray._active is True


def test_hidden_when_idle(qapp, monkeypatch):
    from ydx.core.models import TransferTask

    engine = _StubEngine([
        TransferTask(id=1, kind="download", state="done"),
    ])
    tray = _controller(qapp, engine, monkeypatch)
    tray._refresh_speeds()
    assert not tray.speed_action.isVisible()
    assert tray._active is False
