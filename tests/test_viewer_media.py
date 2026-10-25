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
        headers = {"Content-Length": "5"}

        def iter_content(self, chunk_size):
            yield b"ab"
            yield b"cd"

    seen: list = []
    out = openers._stream_to_file(
        _Resp(), tmp_path / "f.bin", lambda done, total: seen.append((done, total)))
    assert out.read_bytes() == b"abcd"
    assert seen == [(2, 5), (4, 5)]                  # скачано/всего на каждый чанк
    assert not (tmp_path / "f.bin.part").exists()    # временный убран


# --- видео ---------------------------------------------------------------------------


def test_video_without_link_stays_lazy(qapp):
    """Видео без прямой ссылки: окно с сообщением, мультимедиа-стек не трогаем."""
    from ydx.services import i18n
    from ydx.ui.views.viewer import VideoViewer

    i18n.load("ru")
    viewer = VideoViewer(_res("photounlim:/v.mp4", "video"), None, unlim=object())
    qapp.processEvents()

    assert viewer.player is None                     # плеер так и не создан
    assert viewer.status_label.text()
    viewer.close()


def test_video_downloads_with_progress_then_plays(qapp, monkeypatch):
    """REST-видео: скачивание с прогрессом, затем сразу воспроизведение."""
    from ydx.services import i18n, openers
    from ydx.ui.views.viewer import VideoViewer

    i18n.load("ru")
    media = openers.cache_path(_res("disk:/v/clip.mp4", "video"))
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"")

    progress_seen: list = []

    def fake_download(api, resource, target, progress=None):
        if progress:
            progress(4, 10)
            progress(10, 10)
        return target

    monkeypatch.setattr(openers, "_download", fake_download)
    viewer = VideoViewer(_res("disk:/v/clip.mp4", "video"), None, None)
    viewer.show()
    _pump()

    assert viewer.player is not None                 # плеер после скачивания
    viewer.close()


# --- фото ----------------------------------------------------------------------------


def test_unlim_video_downloads_then_plays_normal_window(qapp, _dirs, monkeypatch):
    """Видео безлимита: скачивание с прогрессом → плеер; окно обычное,
    не фуллскрин, без растягивания на весь экран."""
    from PySide6.QtCore import Qt
    from ydx.services import i18n, openers
    from ydx.ui.views.viewer import VideoViewer

    i18n.load("ru")
    media = _dirs / "open" / "v.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"")

    def fake_unlim_download(unlim, resources, target_dir, progress=None):
        if progress:
            progress(resources[0].path, 4, 10)
            progress(resources[0].path, 10, 10)
        return {"done": 1, "skipped": 0, "names": [], "paths": [str(media)]}

    monkeypatch.setattr(openers, "unlim_download", fake_unlim_download)
    viewer = VideoViewer(_res("photounlim:/v.mp4", "video"), None, None,
                         unlim=object())
    viewer.show()
    _pump()

    assert viewer.player is not None                 # играет из скачанного файла
    assert not viewer.windowState() & Qt.WindowState.WindowFullScreen
    assert not viewer.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert viewer.windowTitle() == "v.mp4"
    viewer.close()


def test_video_download_progress_shows_speed_and_left(qapp, _dirs, monkeypatch):
    """Прогресс содержит скачано/всего, скорость и остаток времени."""
    import time as _time

    from ydx.services import i18n, openers
    from ydx.ui.views.viewer import VideoViewer

    i18n.load("ru")
    media = _dirs / "open" / "clip2.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"")

    real_sleep = _time.time

    def fake_download(api, resource, target, progress=None):
        if progress:
            # имитируем секунду загрузки, чтобы спидометр что-то посчитал
            progress(2 * 1024 * 1024, 10 * 1024 * 1024)
            _time.sleep(0.6)                         # noqa: SLF001 — окно спидометра
            progress(5 * 1024 * 1024, 10 * 1024 * 1024)
        return target

    monkeypatch.setattr(openers, "_download", fake_download)
    viewer = VideoViewer(_res("disk:/v/clip2.mp4", "video"), None, None)
    viewer.show()
    _pump(2500)

    text = viewer.status_label.text()
    assert "из" in text                                # «Скачано X из Y»
    viewer.close()


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
