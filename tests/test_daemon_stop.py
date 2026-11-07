"""stop_leftover_daemon: разовый стоп осиротевшего демона."""

from __future__ import annotations

import os

import pytest


@pytest.fixture()
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _patched(monkeypatch, tmp_path, *, binary="/usr/bin/yandex-disk",
             config_exists=True):
    """Подменяем which и startDetached; возвращаем список вызовов."""
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr("shutil.which", lambda _name: binary)

    from ydx.services import daemon_stop

    cfg = tmp_path / "daemon.cfg"
    if config_exists:
        cfg.write_text("dir=~/Yandex.Disk\n", encoding="utf-8")
    monkeypatch.setattr(daemon_stop, "DAEMON_CONFIG_FILE", cfg)
    monkeypatch.setattr(
        "PySide6.QtCore.QProcess.startDetached",
        staticmethod(lambda prog, args: calls.append((prog, args)) or True),
    )
    return calls, str(cfg)


def test_stops_with_binary_and_config(qapp, monkeypatch, tmp_path):
    calls, cfg = _patched(monkeypatch, tmp_path)
    from ydx.services.daemon_stop import stop_leftover_daemon

    assert stop_leftover_daemon() is True
    assert calls == [("/usr/bin/yandex-disk", ["stop", "-c", cfg])]


def test_no_binary_no_call(qapp, monkeypatch, tmp_path):
    calls, _cfg = _patched(monkeypatch, tmp_path, binary=None)
    from ydx.services.daemon_stop import stop_leftover_daemon

    assert stop_leftover_daemon() is False
    assert calls == []


def test_no_app_config_no_call(qapp, monkeypatch, tmp_path):
    """Чужой демон (без нашего daemon.cfg) не трогаем."""
    calls, _cfg = _patched(monkeypatch, tmp_path, config_exists=False)
    from ydx.services.daemon_stop import stop_leftover_daemon

    assert stop_leftover_daemon() is False
    assert calls == []
