"""UnlimDialog: вставка кук, проверка, снятие retranslate при закрытии."""

from __future__ import annotations

import os

import pytest


@pytest.fixture()
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class _StubSettings:
    def __init__(self) -> None:
        self.values: dict = {"show_unlim": True}

    def get(self, key: str):
        return self.values.get(key, True)

    def set(self, key: str, value) -> None:
        self.values[key] = value


class _StubUnlim:
    def __init__(self) -> None:
        self.cookies: str | None = None
        self.check_result = 7
        self.checked: list[str] = []
        self.browser_find: tuple | None = ("Yandex Browser", "Session_id=auto", True)

    @property
    def has_cookies(self) -> bool:
        return self.cookies is not None

    def check(self, raw: str) -> int:
        self.checked.append(raw)
        return self.check_result

    def adopt_browser_cookies(self):
        if self.browser_find is None:
            return None
        browser, header, _changed = self.browser_find
        self.cookies = header
        return browser, header, _changed

    def save_cookies(self, raw: str) -> None:
        if "Session_id" not in raw:
            raise ValueError("no Session_id")
        self.cookies = raw

    def clear_cookies(self) -> None:
        self.cookies = None

    def diagnostic_dump(self, raw=None) -> dict:
        return {"init": {}, "snapshot": {}, "first_clusters": {}}


def _dlg(qapp):
    from ydx.services import i18n
    from ydx.ui.dialogs.unlim_dlg import UnlimDialog

    i18n.load("ru")
    settings, unlim = _StubSettings(), _StubUnlim()
    return UnlimDialog(settings, unlim), unlim


def test_build_and_status(qapp):
    dlg, _unlim = _dlg(qapp)
    assert "не заданы" in dlg.status_label.text()


def test_save_invalid_shows_hint(qapp):
    dlg, unlim = _dlg(qapp)
    dlg.cookie_edit.setText("yandex_uid=1")               # без Session_id
    dlg._save()
    assert unlim.cookies is None
    assert "Session_id" in dlg.status_label.text()


def test_save_and_delete(qapp):
    dlg, unlim = _dlg(qapp)
    dlg.cookie_edit.setText("Session_id=abc; yandex_uid=1")
    dlg._save()
    assert unlim.cookies == "Session_id=abc; yandex_uid=1"
    assert dlg.cookie_edit.text() == ""                   # поле очищено
    assert "подключены" in dlg.status_label.text()

    dlg._delete()
    assert unlim.cookies is None
    assert "не заданы" in dlg.status_label.text()


def test_check_shows_count(qapp):
    dlg, unlim = _dlg(qapp)
    dlg.cookie_edit.setText("Session_id=abc")
    dlg._check()
    import time

    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    end = time.time() + 1.5
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

    assert unlim.checked == ["Session_id=abc"]
    assert "7" in dlg.status_label.text()


def test_close_unregisters_retranslate(qapp):
    from ydx.services import i18n

    dlg, _unlim = _dlg(qapp)
    assert dlg.retranslate in i18n._callbacks
    dlg.close()
    assert dlg.retranslate not in i18n._callbacks


def test_open_web_button_opens_browser(qapp, monkeypatch):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    dlg, _unlim = _dlg(qapp)
    assert "disk.yandex.ru" in dlg.open_web_btn.text()

    opened = []
    monkeypatch.setattr(
        QDesktopServices, "openUrl",
        lambda url: opened.append(url.toString()),
    )
    dlg._open_web()
    assert opened == ["https://disk.yandex.ru/photos"]


def test_autofind_button_reports_count(qapp):
    dlg, unlim = _dlg(qapp)
    dlg._autofind()

    import time

    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    end = time.time() + 1.5
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

    assert unlim.cookies == "Session_id=auto"          # приняты автоматически
    assert "Yandex Browser" in dlg.status_label.text()
    assert "7" in dlg.status_label.text()


def test_autofind_button_nothing_found(qapp):
    dlg, unlim = _dlg(qapp)
    unlim.browser_find = None
    dlg._autofind()

    import time

    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    end = time.time() + 1.5
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)

    assert unlim.cookies is None
    assert "не найден" in dlg.status_label.text()
