"""SettingsDialog: тема/язык/автозапуск, папка загрузок, аккаунт."""

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
        self.values: dict = {"downloads_dir": ""}
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


def _dlg(qapp, settings=None):
    from ydx.services import i18n
    from ydx.ui.dialogs.settings_dlg import SettingsDialog

    i18n.load("ru")
    settings = settings or _StubSettings()
    dlg = SettingsDialog(settings, qapp, None,
                         accounts=_StubAccounts(), unlim=_StubUnlim())
    return dlg, settings


def test_dialog_opens_without_sync(qapp):
    dlg, _settings = _dlg(qapp)
    assert not hasattr(dlg, "_sync_box")
    assert dlg.downloads_label.text()   # папка загрузок заполнена по умолчанию


def test_change_downloads_dir(qapp, monkeypatch, tmp_path):
    dlg, settings = _dlg(qapp)
    monkeypatch.setattr(
        "ydx.ui.widgets.pick_directory", lambda *a, **k: str(tmp_path)
    )
    dlg._change_downloads_dir()
    assert settings.values["downloads_dir"] == str(tmp_path)
    assert dlg.downloads_label.text() == str(tmp_path)
