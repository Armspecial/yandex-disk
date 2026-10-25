"""Альбомы, индекс скачанных, центр ошибок."""

from __future__ import annotations

from pathlib import Path

import pytest


def _img(path: str):
    from ydx.core.models import Resource

    return Resource(name=path.rsplit("/", 1)[-1], type="file", path=path,
                    media_type="image")


@pytest.fixture()
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_downloaded_index_roundtrip_and_watch(qapp, tmp_path: Path, monkeypatch):
    from ydx import constants as const
    from ydx.services.downloaded_index import DownloadedIndex

    monkeypatch.setattr(const, "CONFIG_DIR", tmp_path, raising=False)
    import ydx.services.downloaded_index as di

    monkeypatch.setattr(di, "CONFIG_DIR", tmp_path)

    local = tmp_path / "f.txt"
    local.write_text("v1")
    index = di.DownloadedIndex()
    index.add("disk:/f.txt", str(local))
    assert index.has("disk:/f.txt") and index.local_for("disk:/f.txt") == str(local)

    # персистентность
    index2 = di.DownloadedIndex()
    assert index2.has("disk:/f.txt")

    # изменение файла → сигнал file_changed (после дебаунса)
    fired = []
    index.file_changed.connect(lambda r, l: fired.append((r, l)))
    local.write_text("v2")
    from PySide6.QtCore import QTimer

    deadline_loop(index)
    assert fired and fired[0][0] == "disk:/f.txt"

    # удаление файла → значок снимается
    local.unlink()
    deadline_loop(index)
    assert not index.has("disk:/f.txt")


def deadline_loop(index, ms: int = 2500):
    """Прокрутить цикл событий, пока не сработают таймеры дебаунса."""
    import time

    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def test_error_center_dedup_and_clear(qapp):
    from ydx.services.error_center import ErrorCenter

    center = ErrorCenter()
    changed = []
    center.changed.connect(lambda: changed.append(1))
    center.report("files", "ошибка")
    center.report("files", "ошибка")   # дубль подряд — не добавляется
    center.report("sync", "другая")
    assert center.count == 2
    assert len(changed) == 2
    newest = center.errors[0]
    assert newest.source == "sync"
    center.clear()
    assert center.count == 0
