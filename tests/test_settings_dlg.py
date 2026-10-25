"""SettingsDialog: живой статус синхронизации (точка + текст + тумблер),
галочка автозапуска демона, отписка от статуса при закрытии."""

from __future__ import annotations

import os

import pytest


@pytest.fixture()
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _StubSettings:
    def __init__(self, values: dict | None = None) -> None:
        self.values: dict = {"sync_autostart": True, "downloads_dir": ""}
        if values:
            self.values.update(values)

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value) -> None:
        self.values[key] = value

    @property
    def theme(self) -> str:
        return "system"

    @property
    def language(self) -> str:
        return "ru"


class _StubAccounts:
    active = None


class _StubUnlim:
    has_cookies = False


def _stub_daemon(cfg: dict | None = None):
    from PySide6.QtCore import QObject, Signal

    class _StubDaemon(QObject):
        status_updated = Signal(object)
        installed = True

        def __init__(self) -> None:
            super().__init__()
            self.cfg: dict = {"dir": "/tmp/sync"}
            if cfg:
                self.cfg.update(cfg)
            self.applied: list[dict] = []
            self.started = 0
            self.stopped = 0

        def config(self) -> dict:
            return dict(self.cfg)

        def apply_config(self, updates: dict) -> None:
            self.applied.append(dict(updates))
            self.cfg.update(updates)

        def ensure_config(self) -> bool:
            return True

        def start(self) -> None:
            self.started += 1

        def stop(self) -> None:
            self.stopped += 1

        def sync_now(self) -> None:
            pass

        def cancel_pause(self) -> None:
            pass

        def refresh_status(self) -> None:
            pass

    return _StubDaemon()


def _dlg(qapp, daemon, settings=None):
    from ydx.services import i18n
    from ydx.ui.dialogs.settings_dlg import SettingsDialog

    i18n.load("ru")
    settings = settings or _StubSettings()
    dlg = SettingsDialog(settings, qapp, None, daemon=daemon,
                         accounts=_StubAccounts(), unlim=_StubUnlim())
    return dlg, settings


def test_sync_group_has_status_row(qapp):
    dlg, _settings = _dlg(qapp, _stub_daemon())
    assert hasattr(dlg, "_sync_box")
    assert dlg.sync_state_label.text() == "Статус неизвестен"
    assert not dlg.sync_state_dot.pixmap().isNull()


def test_status_row_reacts_to_signal(qapp):
    from ydx.core.models import DaemonStatus

    daemon = _stub_daemon()
    dlg, _settings = _dlg(qapp, daemon)
    daemon.status_updated.emit(DaemonStatus(state="busy", progress="45% (10 MB/s)"))
    assert "Синхронизация" in dlg.sync_state_label.text()
    assert "45%" in dlg.sync_state_label.text()
    assert dlg.sync_toggle_btn.text() == "Остановить"
    daemon.status_updated.emit(DaemonStatus(state="stopped"))
    assert dlg.sync_toggle_btn.text() == "Запустить"


def test_toggle_button_controls_daemon(qapp):
    from ydx.core.models import DaemonStatus

    daemon = _stub_daemon()
    dlg, _settings = _dlg(qapp, daemon)
    daemon.status_updated.emit(DaemonStatus(state="stopped"))
    dlg.sync_toggle_btn.click()
    assert daemon.started == 1
    daemon.status_updated.emit(DaemonStatus(state="idle"))
    dlg.sync_toggle_btn.click()
    assert daemon.stopped == 1


def test_sync_autostart_checkbox_persists(qapp):
    settings = _StubSettings()
    dlg, _s = _dlg(qapp, _stub_daemon(), settings)
    assert dlg.sync_autostart_check.isChecked()
    dlg.sync_autostart_check.setChecked(False)
    assert settings.values["sync_autostart"] is False
    dlg2, _s2 = _dlg(qapp, _stub_daemon(), settings)
    assert not dlg2.sync_autostart_check.isChecked()


def test_close_stops_status_updates(qapp):
    from ydx.core.models import DaemonStatus

    daemon = _stub_daemon()
    dlg, _settings = _dlg(qapp, daemon)
    dlg.close()
    daemon.status_updated.emit(DaemonStatus(state="busy", progress="45%"))
    assert dlg.sync_state_label.text() == "Статус неизвестен"


def test_no_daemon_hides_sync_group(qapp):
    dlg, _settings = _dlg(qapp, None)
    assert not hasattr(dlg, "_sync_box")


def test_should_autostart_sync():
    from ydx.ui.main_window import should_autostart_sync

    # всё включено — стартуем
    assert should_autostart_sync(True, True, True, "unknown")
    assert should_autostart_sync(True, True, True, "stopped")
    assert should_autostart_sync(True, True, True, "error")
    # демон уже работает — не дёргаем
    for state in ("idle", "busy", "paused", "index"):
        assert not should_autostart_sync(True, True, True, state)
    # не установлен / нет аккаунта / выключено — никогда
    assert not should_autostart_sync(False, True, True, "stopped")
    assert not should_autostart_sync(True, False, True, "stopped")
    assert not should_autostart_sync(True, True, False, "stopped")
