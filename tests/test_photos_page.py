"""Страница «Фото»: сбор медиа из двух источников (автозагрузка + плоский список)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _img(path: str, modified: str):
    from ydx.core.models import Resource

    return Resource(name=path.rsplit("/", 1)[-1], type="file", path=path,
                    media_type="image",
                    modified=datetime.fromisoformat(modified))


@pytest.fixture()
def qapp():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _pump(ms: int = 3000) -> None:
    """Крутить цикл событий, пока не отработают пул потоков и отложенные сигналы."""
    import time

    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


class _StubApi:
    """iter_media отдаёт заданные партии; считает, сколько генераторов создано."""

    def __init__(self, batches: list[list]) -> None:
        self.batches = batches
        self.generators = 0

    def iter_media(self, media_type=None):
        """Как настоящий: отдаёт ресурсы по одному; video-источник пуст."""
        self.generators += 1
        if media_type != "video":
            for batch in self.batches:
                for r in batch:
                    yield r


class _StubAccounts:
    def __init__(self, api) -> None:
        from PySide6.QtCore import QObject, Signal

        # класс с сигналом создаётся динамически: pytest-модуль без QObject-базы
        class _Acc(QObject):
            active_changed = Signal(object)

            def __init__(self, api) -> None:
                super().__init__()
                self._api = api

            @property
            def resources(self):
                return self._api

        self._obj = _Acc(api)

    def __getattr__(self, name):
        return getattr(self._obj, name)


def _page(qapp, batches):
    from ydx.services import i18n
    from ydx.services.thumbs import ThumbService
    from ydx.ui.views.photos_page import PhotosPage

    i18n.load("ru")
    accounts = _StubAccounts(_StubApi(batches))
    page = PhotosPage(accounts, ThumbService(lambda: None))
    return page, accounts._obj


def test_collect_batches_and_default_sort(qapp):
    """Порции добавляются инкрементально; таймлайн отсортирован по дате
    (новые сверху и сгруппированы по дням), даже если свежие пришли последними."""
    batches = [
        [_img(f"disk:/Camera Uploads/old{i}.jpg", f"2020-01-0{i % 9 + 1}T10:00:00+00:00")
         for i in range(203)],
        [_img("disk:/Pictures/new.jpg", "2026-10-23T06:27:05+00:00"),
         _img("disk:/Pictures/mid.jpg", "2023-05-05T05:05:05+00:00")],
    ]
    page, _accounts = _page(qapp, batches)
    adds = []
    orig_add = page.model.add_items
    page.model.add_items = lambda items: (adds.append(len(items)), orig_add(items))
    page.on_shown()
    assert page.model.loading and page.model.rowCount() == 0   # сбор начался
    _pump()

    assert not page.model.loading
    assert page.model.rowCount() == 205
    assert adds == [200, 5]                                    # две порции, как пагинация API
    assert page.sort_proxy.sort_key == "modified"               # «Дата» по умолчанию

    from ydx.ui.views.files_model import ResourceRole
    from ydx.ui.views.photo_timeline import HeaderRole

    tm = page.timeline.timeline_model
    assert page.stack.currentWidget() is page.timeline          # «Все» = таймлайн
    assert tm.index(0, 0).data(ResourceRole) is None            # первая строка — дата
    assert tm.index(0, 0).data(HeaderRole)                      # заголовок дня непустой
    top = tm.index(1, 0).data(ResourceRole)
    assert top.path == "disk:/Pictures/new.jpg"                 # новые сверху


def test_timeline_layout_by_days(qapp):
    """Раскладка: дни по убыванию, в дне — строки по числу колонок, без даты — в конце."""
    from ydx.core.models import Resource
    from ydx.ui.views.photo_timeline import HeaderRole, PhotoTimelineModel
    from ydx.ui.views.files_model import ResourceRole

    photos = (
        [_img(f"disk:/a/a{i}.jpg", f"2026-10-23T1{i}:00:00+00:00") for i in range(4)]
        + [_img("disk:/b/b0.jpg", "2026-10-22T09:00:00+00:00")]
        + [_img("disk:/c/c0.jpg", "2020-01-01T08:00:00+00:00")]
        + [Resource(name="nd.jpg", type="file", path="disk:/nd.jpg", media_type="image")]
    )
    model = PhotoTimelineModel()
    model.set_columns(3)
    model.set_photos(photos)

    assert model.rowCount() == 9        # 4 заголовка + 5 фото-строк (4 фото → 2 строки в дне 10-23)
    assert model.index(0, 0).data(ResourceRole) is None and model.index(0, 0).data(HeaderRole)
    assert [model.index(1, c).data(ResourceRole).path for c in range(3)] == [
        "disk:/a/a3.jpg", "disk:/a/a2.jpg", "disk:/a/a1.jpg"]   # внутри дня — тоже новые-вперёд
    assert model.index(2, 0).data(ResourceRole).path == "disk:/a/a0.jpg"
    assert model.index(2, 1).data(ResourceRole) is None         # хвост строки пуст
    assert model.index(4, 0).data(ResourceRole).path == "disk:/b/b0.jpg"   # 10-22
    assert model.index(6, 0).data(ResourceRole).path == "disk:/c/c0.jpg"   # 2020
    assert model.index(8, 0).data(ResourceRole).path == "disk:/nd.jpg"     # «Без даты» в конце


def test_recollect_discards_stale_chain(qapp):
    """Повторный on_shown до завершения первого сбора: старая цепочка отбрасывается."""
    batches = [
        [_img("disk:/a.jpg", "2024-01-01T00:00:00+00:00")],
    ]
    page, accounts = _page(qapp, batches)
    page.on_shown()
    page.on_shown()          # второй сбор тут же перебивает первый
    _pump()

    assert accounts.resources.generators == 2
    assert page.model.rowCount() == 1                            # только вторая цепочка
    assert page._collected["image"]                              # закэшировано


def test_media_switch_and_cache(qapp):
    """Фото → Видео: отдельный источник; возврат к Фото берёт из кэша сессии."""
    img_batches = [[_img("disk:/i.jpg", "2024-01-01T00:00:00+00:00")]]
    page, _accounts = _page(qapp, img_batches)
    page.on_shown()
    _pump()

    page._set_media("video")
    _pump()
    assert page.model.rowCount() == 0                            # видео в стабе нет
    assert not page.model.loading

    gens_before = page._accounts.resources.generators
    page._set_media("image")
    _pump()
    assert page.model.rowCount() == 1                            # из кэша, без сети
    assert page._accounts.resources.generators == gens_before


def test_active_changed_invalidates_cache(qapp):
    batches = [[_img("disk:/i.jpg", "2024-01-01T00:00:00+00:00")]]
    page, accounts = _page(qapp, batches)
    page.on_shown()
    _pump()
    assert page._collected

    accounts.active_changed.emit(object())                       # смена аккаунта
    assert not page._collected                                   # кэш сброшен

    page.on_shown()
    _pump()
    assert accounts.resources.generators == 2                    # сбор пошёл заново


def test_timeline_rebuild_keeps_all_photos_visible(qapp):
    """Пересборка ленты (порция новее сдвигает строки) не должна прятать фото:
    спаны заголовков прошлой раскладки накрывали ячейки, и день «терял» снимки."""
    from ydx.services.thumbs import ThumbService
    from ydx.ui.views.files_model import ResourceRole
    from ydx.ui.views.photo_timeline import PhotoTimelineView

    day28 = [_img(f"disk:/u/28-{i}.jpg", f"2026-09-28T1{i % 10}:00:00+00:00") for i in range(16)]
    day27 = [_img(f"disk:/u/27-{i}.jpg", f"2026-09-27T1{i % 10}:00:00+00:00") for i in range(7)]
    day26 = [_img(f"disk:/u/26-{i}.jpg", f"2026-09-26T1{i % 10}:00:00+00:00") for i in range(6)]

    view = PhotoTimelineView(ThumbService(lambda: None))
    view.resize(900, 400)                       # 5 колонок
    view.show()
    view.set_photos(day28 + day27 + day26)      # первая раскладка: спаны заголовков
    qapp.processEvents()
    view.set_photos([_img("disk:/u/new.jpg", "2026-10-25T09:00:00+00:00")]
                    + day28 + day27 + day26)    # порция новнее — строки сдвинулись
    qapp.processEvents()

    model = view.timeline_model
    hidden = [
        (row, col)
        for row in range(model.rowCount())
        for col in range(model._cols)
        if model.index(row, col).data(ResourceRole) is not None
        and view.isIndexHidden(model.index(row, col))
    ]
    assert not hidden                           # каждая ячейка с фото видима
    assert sum(len(r["photos"]) for r in model._rows if "photos" in r) == 30
