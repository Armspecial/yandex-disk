"""Страница «Фото» + безлимит: объединённая лента, ошибки, скачивание."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest


def _res(path: str, modified: str, media: str = "image", **over):
    from ydx.core.models import Resource

    return Resource(name=path.rsplit("/", 1)[-1], type="file", path=path,
                    media_type=media,
                    modified=datetime.fromisoformat(modified), **over)


def _pump(ms: int = 3000) -> None:
    import time

    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


class _StubApi:
    def __init__(self, batches: list[list]) -> None:
        self.batches = batches

    def iter_media(self, media_type=None):
        if media_type != "video":
            for batch in self.batches:
                yield from batch


class _StubAccounts:
    def __init__(self, api) -> None:
        from PySide6.QtCore import QObject, Signal

        class _Acc(QObject):
            active_changed = Signal(object)

            def __init__(self) -> None:
                super().__init__()
                self._api = api

            @property
            def resources(self):
                return self._api

        self._obj = _Acc()


class _StubUnlim:
    """Безлимит: QObject-обёртка с cookies_changed, как настоящий сервис."""

    def __init__(self, items=None, error: Exception | None = None) -> None:
        from PySide6.QtCore import QObject, Signal

        class _Impl(QObject):
            cookies_changed = Signal()
            dates_changed = Signal()

        self._obj = _Impl()
        self.items = items or []
        self.error = error
        self.seen_media: list = []
        self.enabled = True
        self.has_cookies = True
        self.overrides: dict = {}
        self._redates_fresh = False
        self.refreshed: list = []

    @property
    def cookies_changed(self):
        return self._obj.cookies_changed

    @property
    def dates_changed(self):
        return self._obj.dates_changed

    def redates_fresh(self) -> bool:
        return self._redates_fresh

    def date_overrides(self) -> dict:
        return self.overrides

    def refresh_date_overrides(self, stamped) -> int:
        self.refreshed.append(stamped)
        return 0

    def iter_media(self, media_type=None):
        self.seen_media.append(media_type)
        if self.error is not None:
            raise self.error
        for r in self.items:
            if media_type is None or r.media_type == media_type:
                yield r


class _StubSettings:
    def __init__(self, values: dict | None = None) -> None:
        self.values = values or {}

    def get(self, key: str):
        return self.values.get(key, True)


class _StubEngine:
    def __init__(self) -> None:
        self.downloads: list = []

    def enqueue_download(self, resource, path: str) -> None:
        self.downloads.append((resource.path, path))


def _page(qapp, batches, unlim, tmp_path, engine=None):
    from ydx.services import i18n
    from ydx.services.thumbs import ThumbService
    from ydx.ui.views.photos_page import PhotosPage

    i18n.load("ru")
    accounts = _StubAccounts(_StubApi(batches))
    settings = _StubSettings({"downloads_dir": str(tmp_path / "dl")})
    page = PhotosPage(accounts._obj, ThumbService(lambda: None), None,
                      unlim=unlim, engine=engine or _StubEngine(),
                      settings=settings)
    return page


@pytest.fixture()
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_merged_feed_and_album(qapp, tmp_path):
    """REST + безлимит в одной ленте; альбом безлимита назван по i18n."""
    unlim = _StubUnlim([
        _res("photounlim:/u1.jpg", "2026-10-24T09:00:00+00:00",
             file_url="https://dl/u1.jpg"),
        _res("photounlim:/u2.jpg", "2026-10-23T09:00:00+00:00"),
    ])
    page = _page(qapp, [[_res("disk:/Cam/a.jpg", "2020-01-01T00:00:00+00:00")]],
                 unlim, tmp_path)
    page.on_shown()
    _pump()

    assert not page.model.loading
    assert page.model.rowCount() == 3                     # 1 REST + 2 безлимит
    from ydx.ui.views.files_model import ResourceRole

    top = page.timeline.timeline_model.index(1, 0).data(ResourceRole)
    assert top.path == "photounlim:/u1.jpg"               # новейшие сверху

    page._switch_mode("albums")
    names = {a.path: a.name for a in page.albums_model.items}
    assert names["photounlim:"] == "Безлимитная автозагрузка"


def test_unlim_failure_keeps_rest_feed(qapp, tmp_path):
    """Падение безлимита не роняет REST-ленту; полоса показывает ошибку."""
    from ydx.core.errors import UnlimAuthError

    unlim = _StubUnlim(error=UnlimAuthError("Session expired"))
    page = _page(qapp, [[_res("disk:/a.jpg", "2024-01-01T00:00:00+00:00")]],
                 unlim, tmp_path)
    page.on_shown()
    _pump()

    assert page.model.rowCount() == 1                     # REST собран
    assert not page.model.loading
    assert not page.unlim_strip.isHidden()
    assert "недоступна" in page.unlim_strip.text()


def test_hint_shown_without_cookies(qapp, tmp_path):
    """Кук нет, но безлимит включён → подсказка «подключите куки»."""
    unlim = _StubUnlim([])
    unlim.has_cookies = False
    page = _page(qapp, [[_res("disk:/a.jpg", "2024-01-01T00:00:00+00:00")]],
                 unlim, tmp_path)
    page.on_shown()
    _pump()

    assert page.model.rowCount() == 1
    assert not page.unlim_strip.isHidden()
    assert "куки" in page.unlim_strip.text().lower()


def test_cookies_changed_invalidates(qapp, tmp_path):
    from datetime import datetime  # noqa: F401

    unlim = _StubUnlim([_res("photounlim:/u.jpg", "2026-01-01T00:00:00+00:00")])
    page = _page(qapp, [[]], unlim, tmp_path)             # REST пуст
    page.on_shown()
    _pump()
    assert page._collected["image"]

    unlim.cookies_changed.emit()
    assert not page._collected                            # кэш сброшен


def test_media_toggle_reaches_unlim(qapp, tmp_path):
    unlim = _StubUnlim([_res("photounlim:/v.mp4", "2026-01-01T00:00:00+00:00",
                             "video")])
    page = _page(qapp, [[]], unlim, tmp_path)
    page.on_shown()
    _pump()
    assert unlim.seen_media == ["image"]

    page._set_media("video")
    _pump()
    assert unlim.seen_media == ["image", "video"]
    assert page.model.rowCount() == 1


def test_exif_overrides_regroup_rest_photos(qapp, tmp_path):
    """REST-фото с датой изменения ≠ дате съёмки: в ленте — дата съёмки."""
    unlim = _StubUnlim([])
    unlim.overrides = {
        "disk:/Cam/rescued.jpg":
            datetime.fromisoformat("2026-10-07T19:53:40+00:00"),
    }
    page = _page(qapp, [[_res("disk:/Cam/rescued.jpg", "2026-10-20T10:00:00+00:00")]],
                 unlim, tmp_path)
    page.on_shown()
    _pump()

    assert page.model.items[0].modified == datetime.fromisoformat(
        "2026-10-07T19:53:40+00:00")                     # EXIF, не 20-е число
    assert unlim.refreshed and unlim.refreshed[0][0][0] == "disk:/Cam/rescued.jpg"


def test_dates_changed_invalidates_cache(qapp, tmp_path):
    unlim = _StubUnlim([_res("photounlim:/u.jpg", "2026-01-01T00:00:00+00:00")])
    page = _page(qapp, [[]], unlim, tmp_path)
    page.on_shown()
    _pump()
    assert page._collected["image"]

    unlim.dates_changed.emit()                           # даты уточнились
    assert not page._collected


def test_download_selected_splits_sources(qapp, tmp_path, monkeypatch):
    """REST → движок передач, безлимит → unlim_download по прямым ссылкам."""
    from ydx.services import openers

    engine = _StubEngine()
    unlim = _StubUnlim([])
    page = _page(qapp, [[]], unlim, tmp_path, engine=engine)

    calls = {}

    def fake_unlim_download(unlim_arg, items, target_dir):
        calls["items"] = [r.path for r in items]
        calls["dir"] = str(target_dir)
        return {"done": 1, "skipped": 1, "names": ["no-link.jpg"]}

    monkeypatch.setattr(openers, "unlim_download", fake_unlim_download)

    reports = []

    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox, "information",
        lambda *a, **k: reports.append(a[2]) or QMessageBox.StandardButton.Ok,
    )

    page._download_selected([
        _res("disk:/Photos/p.jpg", "2024-01-01T00:00:00+00:00"),
        _res("photounlim:/u.jpg", "2026-01-01T00:00:00+00:00",
             file_url="https://dl/u.jpg"),
        _res("photounlim:/no-link.jpg", "2025-01-01T00:00:00+00:00"),
    ])
    _pump()

    assert engine.downloads == [(
        "disk:/Photos/p.jpg", str(tmp_path / "dl" / "Photos" / "p.jpg"))]
    assert calls["items"] == ["photounlim:/u.jpg", "photounlim:/no-link.jpg"]
    assert calls["dir"] == str(tmp_path / "dl")
    assert reports and "Скачано: 1" in reports[0]
    assert "Пропущено без прямой ссылки: 1" in reports[0]
