"""Недавние: «Скачать» в контекстном меню — как в Файлах."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest


@pytest.fixture()
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _res(name: str, path: str, type_: str = "file"):
    from ydx.core.models import Resource

    return Resource(
        name=name, type=type_, path=path, media_type="image", size=10,
        modified=datetime(2026, 10, 25, 12, tzinfo=timezone.utc),
    )


class _Engine:
    """Заглушка TransferEngine: помнит поставленные в очередь скачивания."""

    def __init__(self):
        self.downloads: list[tuple[str, str]] = []

    def enqueue_download(self, resource, local_path: str) -> None:
        self.downloads.append((resource.path, local_path))


class _Settings:
    def __init__(self, downloads_dir: str):
        self._dir = downloads_dir

    def get(self, key, default=None):
        return self._dir if key == "downloads_dir" else default

    def set(self, key, value) -> None:
        pass


def _page(qapp, engine, settings, downloaded=None):
    from PySide6.QtCore import QObject, Signal

    from ydx.services import i18n
    from ydx.ui.views.recent_page import RecentPage

    i18n.load("ru")

    class _Thumbs(QObject):
        ready = Signal(str)

        def pixmap(self, *_a, **_k):
            return None

    class _Acc:
        class _A:
            login = "u"

        active = _A()
        accounts = []
        resources = None

    return RecentPage(_Acc(), _Thumbs(), None, engine=engine,
                      settings=settings, downloaded=downloaded)


def i18n_tr(key: str) -> str:
    from ydx.services import i18n

    return i18n.tr(key)


def test_download_selected_mirrors_cloud_paths(qapp, tmp_path):
    engine = _Engine()
    page = _page(qapp, engine, _Settings(str(tmp_path)))

    page._download_selected([
        _res("клип.mp4", "disk:/Фото2026/клип.mp4"),
        _res("заметка.txt", "disk:/заметка.txt"),
    ])
    assert [p for p, _ in engine.downloads] == [
        "disk:/Фото2026/клип.mp4", "disk:/заметка.txt",
    ]
    # структура облака повторяется локально (как в Файлах)
    assert engine.downloads[0][1] == str(tmp_path / "Фото2026" / "клип.mp4")


def test_download_selected_skips_dirs(qapp, tmp_path):
    engine = _Engine()
    page = _page(qapp, engine, _Settings(str(tmp_path)))

    page._download_selected([_res("Фото2026", "disk:/Фото2026", "dir")])
    assert engine.downloads == []


def test_context_menu_downloads_file(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMenu

    class _Menu(QMenu):
        captured: list[QMenu] = []

        def exec(self, pos=None):     # не блокируем цикл — просто перехват
            _Menu.captured.append(self)
            return None

    monkeypatch.setattr("ydx.ui.views.recent_page.QMenu", _Menu)

    engine = _Engine()
    page = _page(qapp, engine, _Settings(str(tmp_path)))
    page._context_menu(None, [_res("a.jpg", "disk:/a.jpg")])

    assert _Menu.captured
    act = next(a for a in _Menu.captured[-1].actions() if a.text() == "Скачать")
    act.trigger()
    assert engine.downloads == [("disk:/a.jpg", str(tmp_path / "a.jpg"))]


def test_context_menu_no_download_for_folder(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMenu

    class _Menu(QMenu):
        captured: list[QMenu] = []

        def exec(self, pos=None):
            _Menu.captured.append(self)
            return None

    monkeypatch.setattr("ydx.ui.views.recent_page.QMenu", _Menu)

    engine = _Engine()
    page = _page(qapp, engine, _Settings(str(tmp_path)))
    page._context_menu(None, [_res("Фото2026", "disk:/Фото2026", "dir")])

    assert _Menu.captured
    assert "Скачать" not in [a.text() for a in _Menu.captured[-1].actions()]


# --- «Убрать с компьютера (оставить в облаке)» -----------------------------------


class _Downloaded:
    """Заглушка DownloadedIndex: remote_path → local_path."""

    def __init__(self, mapping: dict[str, str]):
        self._data = dict(mapping)
        self.removed: list[str] = []

    def has(self, remote_path: str) -> bool:
        return remote_path in self._data

    def local_for(self, remote_path: str) -> str | None:
        return self._data.get(remote_path)

    def remove(self, remote_path: str) -> None:
        self._data.pop(remote_path, None)
        self.removed.append(remote_path)


def _free_page(qapp, tmp_path, monkeypatch, downloaded):
    monkeypatch.setattr(
        "ydx.ui.views.recent_page.confirm", lambda *a, **k: True
    )
    return _page(qapp, _Engine(), _Settings(str(tmp_path)),
                 downloaded=downloaded)


def test_free_up_removes_local_keeps_cloud(qapp, tmp_path, monkeypatch):
    local = tmp_path / "a.jpg"
    local.write_bytes(b"data")
    downloaded = _Downloaded({"disk:/a.jpg": str(local)})
    page = _free_page(qapp, tmp_path, monkeypatch, downloaded)

    page._free_up([_res("a.jpg", "disk:/a.jpg")])
    assert not local.exists()               # локальная копия удалена
    assert downloaded.removed == ["disk:/a.jpg"]   # из индекса offline убрана


def test_free_up_without_downloaded_shows_info(qapp, tmp_path, monkeypatch):
    """Незнакомый файл не удаляем: просто сообщение, ничего не трогаем."""
    downloaded = _Downloaded({})
    page = _free_page(qapp, tmp_path, monkeypatch, downloaded)

    from PySide6.QtWidgets import QMessageBox

    shown = []
    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **k: shown.append(True) or QMessageBox.StandardButton.Ok,
    )
    page._free_up([_res("a.jpg", "disk:/a.jpg")])
    assert shown
    assert downloaded.removed == []


def test_context_menu_free_up_only_for_downloaded(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMenu

    class _Menu(QMenu):
        captured: list[QMenu] = []

        def exec(self, pos=None):
            _Menu.captured.append(self)
            return None

    monkeypatch.setattr("ydx.ui.views.recent_page.QMenu", _Menu)
    monkeypatch.setattr(
        "ydx.ui.views.recent_page.confirm", lambda *a, **k: True
    )

    local = tmp_path / "a.jpg"
    local.write_bytes(b"data")
    label = i18n_tr("ctx.free_up")

    # скачанный файл → пункт есть, триггер удаляет локальную копию
    downloaded = _Downloaded({"disk:/a.jpg": str(local)})
    page = _page(qapp, _Engine(), _Settings(str(tmp_path)),
                 downloaded=downloaded)
    page._context_menu(None, [_res("a.jpg", "disk:/a.jpg")])
    act = next(a for a in _Menu.captured[-1].actions() if a.text() == label)
    act.trigger()
    assert not local.exists()

    # не скачанный → пункта нет
    _Menu.captured.clear()
    page._context_menu(None, [_res("b.jpg", "disk:/b.jpg")])
    assert label not in [a.text() for a in _Menu.captured[-1].actions()]
