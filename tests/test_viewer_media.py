"""Просмотрщики: прогресс скачивания «X из Y», ленивый плеер, полные фото."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest


@pytest.fixture()
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    """Кэши — во временный каталог, тесты не трогают ~/.cache."""
    from ydx import constants as const

    monkeypatch.setattr(const, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(const, "OPEN_CACHE_DIR", tmp_path / "open")
    return tmp_path


def _pump(ms: int = 1500) -> None:
    import time

    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def _res(path: str, media: str, size: int = 10):
    from ydx.core.models import Resource

    return Resource(name=path.rsplit("/", 1)[-1], type="file", path=path,
                    media_type=media, size=size,
                    modified=datetime(2026, 9, 28, 16, 4, tzinfo=timezone.utc))


# --- загрузчик ----------------------------------------------------------------------


def test_stream_to_file_reports_progress(tmp_path):
    from ydx.services import openers

    class _Resp:
        headers = {"Content-Length": "4"}

        def iter_content(self, chunk_size):
            yield b"ab"
            yield b"cd"

        def close(self):
            pass

    seen: list = []
    out = openers._stream_to_file(
        _Resp(), tmp_path / "f.bin", lambda done, total: seen.append((done, total)))
    assert out.read_bytes() == b"abcd"
    assert seen == [(2, 4), (4, 4)]                  # скачано/всего на каждый чанк
    assert not (tmp_path / "f.bin.part").exists()    # временный убран


# --- видео ---------------------------------------------------------------------------


def test_video_without_link_shows_error(qapp):
    """Видео безлимита без сервиса: сообщение об ошибке, окно живо."""
    from ydx.services import i18n
    from ydx.ui.views.viewer import VideoViewer

    i18n.load("ru")
    viewer = VideoViewer(_res("photounlim:/v.mp4", "video"), None, None)
    qapp.processEvents()

    assert viewer.status_label.text()          # сообщение показано
    assert not getattr(viewer, "_sys_btn", None)
    viewer.close()


def test_video_downloads_then_opens_system_player(qapp, _dirs, monkeypatch):
    """REST-видео: прогресс скачивания, затем файл открывается системным
    плеером, окно закрывается; окно обычное, не фуллскрин."""
    from PySide6.QtCore import Qt

    from ydx.services import i18n, openers
    import ydx.ui.views.viewer as viewer_mod
    from ydx.ui.views.viewer import VideoViewer

    i18n.load("ru")
    media = _dirs / "open" / "clip.mp4"
    opened = []
    monkeypatch.setattr(viewer_mod, "open_in_system_player",
                        lambda path: opened.append(path) or True)

    def fake_download(api, resource, target, progress=None, abort=None):
        if progress:
            progress(4, 10)
            progress(10, 10)
        media.parent.mkdir(parents=True, exist_ok=True)
        media.write_bytes(b"")
        return media

    monkeypatch.setattr(openers, "_download", fake_download)
    viewer = VideoViewer(_res("disk:/v/clip.mp4", "video"), None, None)
    viewer.show()
    assert not viewer.windowState() & Qt.WindowState.WindowFullScreen
    assert viewer.windowTitle() == "clip.mp4"
    _pump()

    assert opened == [str(media)]              # открыт системным плеером
    try:
        viewer.isVisible()
        closed = False
    except RuntimeError:                       # WA_DeleteOnClose уже снёс окно
        closed = True
    assert closed                              # окно закрылось после передачи


def test_unlim_video_downloads_then_opens_system_player(qapp, _dirs, monkeypatch):
    """Видео безлимита: скачивание → системный плеер."""
    from ydx.services import i18n, openers
    import ydx.ui.views.viewer as viewer_mod
    from ydx.ui.views.viewer import VideoViewer

    i18n.load("ru")
    media = _dirs / "open" / "v.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"")
    opened = []
    monkeypatch.setattr(viewer_mod, "open_in_system_player",
                        lambda path: opened.append(path) or True)

    def fake_unlim_download(unlim, resources, target_dir, progress=None,
                            abort=None):
        if progress:
            progress(resources[0].path, 4, 10)
            progress(resources[0].path, 10, 10)
        return {"done": 1, "skipped": 0, "names": [], "paths": [str(media)],
                "errors": {}}

    monkeypatch.setattr(openers, "unlim_download", fake_unlim_download)
    viewer = VideoViewer(_res("photounlim:/v.mp4", "video"), None, None,
                         unlim=object())
    viewer.show()
    _pump()

    assert opened == [str(media)]


def test_progress_text_contains_speed_and_left():
    """«Скачано X из Y · N/с · осталось T» — скорость и остаток на месте."""
    from ydx.services import i18n
    from ydx.ui.views.viewer import _progress_text

    i18n.load("ru")
    text = _progress_text(5 * 1024 * 1024, 10 * 1024 * 1024,
                          1024 * 1024)      # 1 МБ/с → ~5 с остатка
    assert "из" in text and "/с" in text and "осталось" in text


def test_photo_viewer_loads_original_from_unlim(qapp):
    """Полноэкранное фото: оригинал качается с прогрессом и заменяет миниатюру."""
    from PySide6.QtCore import QBuffer
    from PySide6.QtGui import QImage
    from ydx.services import openers
    from ydx.services.thumbs import ThumbService
    from ydx.ui.views.viewer import PhotoViewer

    img = QImage(4, 4, QImage.Format.Format_RGB32)
    img.fill(0xFF00FF00)
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.ReadWrite)
    img.save(buf, "PNG")
    data = bytes(buf.data())

    class _Resp:
        status_code = 200
        headers = {"Content-Length": str(len(data))}

        def iter_content(self, chunk_size):
            half = len(data) // 2
            yield data[:half]
            yield data[half:]

    class _Unlim:
        def download_links(self, resources):
            return {resources[0].path: "https://x/orig"}

        def fetch(self, url, stream=False):
            return _Resp()

    resource = _res("photounlim:/p.png", "image", size=len(data))
    viewer = PhotoViewer([resource], 0, ThumbService(lambda: None, lambda: None),
                         unlim=_Unlim())
    viewer.show()
    _pump()

    assert viewer._full is not None and not viewer._full.isNull()
    assert openers.cache_path(resource).exists()     # оригинал остался в кэше
    viewer.close()


# --- залипание и отмена ---------------------------------------------------------------


def test_stream_aborts_when_stalled(tmp_path):
    """Узел «дозирует» байты: без движения дольше stall_timeout загрузка
    обрывается, файл не появляется."""
    import time

    import pytest

    from ydx.services import openers

    class _SlowResp:
        headers = {"Content-Length": "10"}

        def iter_content(self, chunk_size):
            yield b"ab"
            time.sleep(1.2)                       # дольше stall_timeout=0.5
            yield b"cd"

        def close(self):
            pass

    with pytest.raises(Exception):
        openers._stream_to_file(_SlowResp(), tmp_path / "s.bin",
                                stall_timeout=0.5)
    assert not (tmp_path / "s.bin").exists()
    assert not (tmp_path / "s.bin.part").exists()


def test_stream_aborts_on_cancel(tmp_path):
    import threading

    import pytest

    from ydx.services import openers

    abort = threading.Event()
    abort.set()

    class _Resp:
        headers = {"Content-Length": "4"}

        def iter_content(self, chunk_size):
            yield b"ab"
            yield b"cd"

        def close(self):
            pass

    with pytest.raises(openers.DownloadCancelled):
        openers._stream_to_file(_Resp(), tmp_path / "c.bin", abort=abort)
    assert not (tmp_path / "c.bin.part").exists()


def test_unlim_download_retries_with_fresh_link(tmp_path):
    """Первая ссылка умерла на середине — вторая попытка со свежей ссылкой
    доигрывает файл."""
    from ydx.core.models import Resource
    from ydx.services import openers

    class _Resp:
        status_code = 200
        headers = {"Content-Length": "4"}

        def __init__(self, data):
            self._data = data

        def iter_content(self, chunk_size):
            yield self._data

        def close(self):
            pass

    class _Unlim:
        def __init__(self):
            self.fetches = 0

        def download_links(self, resources):
            return {resources[0].path: "https://x/dead"}

        def item_url(self, path):
            return "https://x/fresh"

        def fetch(self, url, stream=False):
            self.fetches += 1
            if "dead" in url:
                raise ConnectionError("node dropped")
            return _Resp(b"data")

    res = Resource(name="v.mp4", type="file", path="photounlim:/v.mp4",
                   media_type="video", size=4)
    out = openers.unlim_download(_Unlim(), [res], tmp_path)
    assert out["done"] == 1 and out["paths"] == [str(tmp_path / "v.mp4")]
    assert (tmp_path / "v.mp4").read_bytes() == b"data"


def test_video_close_cancels_download(qapp, _dirs, monkeypatch):
    """Закрытие окна видео сразу отменяет идущую загрузку."""
    import threading

    from ydx.services import i18n, openers
    from ydx.ui.views.viewer import VideoViewer

    i18n.load("ru")
    captured = {}

    def fake_unlim_download(unlim, resources, target_dir, progress=None,
                            abort=None):
        captured["abort"] = abort
        abort.wait(timeout=5)                     # «грузим», пока не отменят
        raise openers.DownloadCancelled("cancelled")

    monkeypatch.setattr(openers, "unlim_download", fake_unlim_download)
    viewer = VideoViewer(_res("photounlim:/v.mp4", "video"), None, None,
                         unlim=object())
    viewer.show()
    qapp.processEvents()

    deadline = __import__("time").time() + 3
    while __import__("time").time() < deadline and "abort" not in captured:
        qapp.processEvents()
        __import__("time").sleep(0.02)
    assert "abort" in captured and not captured["abort"].is_set()

    viewer.close()
    qapp.processEvents()
    assert captured["abort"].is_set()             # закрытие отменило загрузку
