"""SyncDialog: галочка = синхронизируется, «Обновить» перечитывает список
и не теряет незаписанные правки; статус управляет кнопками."""

from __future__ import annotations

import os
import time

import pytest


@pytest.fixture()
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _stub_daemon(cfg: dict | None = None):
    """QObject-заглушка демона: только то, что трогает SyncDialog."""
    from PySide6.QtCore import QObject, Signal

    class _StubDaemon(QObject):
        status_updated = Signal(object)
        installed = True

        def __init__(self) -> None:
            super().__init__()
            self.cfg: dict = {"dir": "/tmp/sync", "exclude-dirs": ""}
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

        def pause_for(self, hours: float) -> None:
            pass

        def cancel_pause(self) -> None:
            pass

        def refresh_status(self) -> None:
            pass

    return _StubDaemon()


class _StubResources:
    def __init__(self, dir_names: list[str]) -> None:
        self.dir_names = dir_names
        self.calls = 0

    def list_page(self, path: str, limit: int = 100):
        from ydx.core.models import Resource

        self.calls += 1
        items = [Resource(name=n, type="dir", path=f"disk:/{n}") for n in self.dir_names]
        return items, len(items)


def _pump(qapp, seconds: float = 1.5) -> None:
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


def _dlg(qapp, daemon, resources):
    from PySide6.QtCore import Qt

    from ydx.services import i18n
    from ydx.ui.panels.sync_panel import SyncDialog

    i18n.load("ru")
    return SyncDialog(daemon, resources, None), Qt


def _item(dlg, name):
    for i in range(dlg.tree.topLevelItemCount()):
        item = dlg.tree.topLevelItem(i)
        if item.text(0) == name:
            return item
    return None


def test_excluded_folder_shows_unchecked(qapp):
    daemon = _stub_daemon({"exclude-dirs": "Фото"})
    dlg, Qt = _dlg(qapp, daemon, _StubResources(["Фото", "Документы", "Музыка"]))
    _pump(qapp, 0.5)
    assert _item(dlg, "Фото").checkState(0) == Qt.CheckState.Unchecked
    assert _item(dlg, "Документы").checkState(0) == Qt.CheckState.Checked
    assert _item(dlg, "Музыка").checkState(0) == Qt.CheckState.Checked


def test_apply_unchecked_becomes_excluded(qapp):
    daemon = _stub_daemon()
    dlg, Qt = _dlg(qapp, daemon, _StubResources(["Документы", "Фото"]))
    _pump(qapp, 0.5)
    _item(dlg, "Документы").setCheckState(0, Qt.CheckState.Unchecked)
    dlg._apply()
    updates = next(u for u in daemon.applied if "exclude-dirs" in u)
    assert updates["exclude-dirs"] == "Документы"


def test_apply_preserves_exclude_subpaths(qapp):
    daemon = _stub_daemon({"exclude-dirs": "Фото,Фото/2019"})
    dlg, Qt = _dlg(qapp, daemon, _StubResources(["Документы", "Фото"]))
    _pump(qapp, 0.5)
    _item(dlg, "Документы").setCheckState(0, Qt.CheckState.Unchecked)
    dlg._apply()
    updates = next(u for u in daemon.applied if "exclude-dirs" in u)
    from ydx.core.daemon_config import parse_exclude_dirs

    assert set(parse_exclude_dirs(updates["exclude-dirs"])) == {
        "Документы", "Фото", "Фото/2019",
    }


def test_apply_without_tree_keeps_exclude_dirs(qapp):
    daemon = _stub_daemon({"exclude-dirs": "Фото"})
    dlg, _Qt = _dlg(qapp, daemon, None)   # список не загрузился
    dlg._apply()
    assert all("exclude-dirs" not in u for u in daemon.applied)


def test_refresh_button_refetches(qapp):
    resources = _StubResources(["Документы"])
    dlg, _Qt = _dlg(qapp, _stub_daemon(), resources)
    _pump(qapp, 0.5)
    assert resources.calls == 1
    dlg.refresh_btn.click()
    _pump(qapp, 0.5)
    assert resources.calls == 2
    assert dlg.refresh_btn.isEnabled()


def test_refresh_keeps_pending_unchecks(qapp):
    resources = _StubResources(["Документы", "Фото"])
    dlg, Qt = _dlg(qapp, _stub_daemon(), resources)
    _pump(qapp, 0.5)
    _item(dlg, "Документы").setCheckState(0, Qt.CheckState.Unchecked)
    resources.dir_names.append("Новая")   # в облаке появилась папка
    dlg.refresh_btn.click()
    _pump(qapp, 0.5)
    assert _item(dlg, "Документы").checkState(0) == Qt.CheckState.Unchecked
    assert _item(dlg, "Новая").checkState(0) == Qt.CheckState.Checked


def test_status_updates_buttons_and_labels(qapp):
    from ydx.core.models import DaemonStatus

    daemon = _stub_daemon()
    dlg, _Qt = _dlg(qapp, daemon, _StubResources(["Документы"]))
    daemon.status_updated.emit(DaemonStatus(state="idle"))
    assert dlg.state_label.text() == "Синхронизировано"
    assert not dlg.start_btn.isEnabled()
    assert dlg.stop_btn.isEnabled()
    daemon.status_updated.emit(DaemonStatus(state="stopped"))
    assert dlg.start_btn.isEnabled()
    assert not dlg.stop_btn.isEnabled()


def test_no_key_echo(qapp):
    dlg, _Qt = _dlg(qapp, _stub_daemon(), _StubResources(["Документы"]))
    assert "Папки" in dlg.selective_title.text()
    assert "sync." not in dlg.selective_title.text()
    assert dlg.refresh_btn.text() == "Обновить"
