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


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path, monkeypatch):
    """Дисковый кэш лент — во временный каталог, тесты не трогают ~/.cache."""
    from ydx import constants as const

    monkeypatch.setattr(const, "CACHE_DIR", tmp_path)
    return tmp_path


class _StubApi:
    """iter_media отдаёт заданные партии; считает, сколько генераторов создано."""

    def __init__(self, batches: list[list], revision: int = 7) -> None:
        self.batches = batches
        self.generators = 0
        self._rev = revision
        self.revision_calls = 0

    def revision(self) -> int:
        self.revision_calls += 1
        return self._rev

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


def _page_from(qapp, accounts):
    """Страница «Фото» на готовом стабе аккаунтов."""
    from ydx.services import i18n
    from ydx.services.thumbs import ThumbService
    from ydx.ui.views.photos_page import PhotosPage

    i18n.load("ru")
    page = PhotosPage(accounts, ThumbService(lambda: None))
    return page, accounts._obj


def _page(qapp, batches):
    return _page_from(qapp, _StubAccounts(_StubApi(batches)))


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
    """Повторный on_shown до завершения первого сбора: старая цепочка
    отбрасывается — в модели остаются только элементы второй."""
    import threading

    class _SlowApi:
        """Обход висит на «воротах»; каждая цепочка отдаёт свой файл."""

        def __init__(self) -> None:
            self.gate = threading.Event()
            self.calls = 0

        def revision(self) -> int:
            return 7

        def iter_media(self, media_type=None):
            self.calls += 1
            self.gate.wait(timeout=10)
            yield _img(f"disk:/from-call-{self.calls}.jpg",
                       "2024-01-01T00:00:00+00:00")

    accounts = _StubAccounts(_SlowApi())
    page, _acc = _page_from(qapp, accounts)
    page.on_shown()                       # первая цепочка повисла на воротах
    page.on_shown()                       # вторая тут же перебивает
    accounts.resources.gate.set()         # отпускаем обе
    _pump(5000)

    assert accounts.resources.calls == 2
    assert page.model.rowCount() == 1     # только вторая цепочка
    assert page.model.items[0].path == "disk:/from-call-2.jpg"
    assert page._collected["image"]       # завершённая цепочка закэширована


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


def test_tile_slider_relayouts_and_persists(qapp):
    """Слайдер размера плиток: плитка и число колонок меняются, все фото
    остаются видимыми, выбор сохраняется в настройках."""
    from ydx.ui.views.files_model import ResourceRole
    from ydx.ui.views.photo_timeline import TILE_MAX, TILE_MIN

    batches = [[_img(f"disk:/t{i:02d}.jpg", f"2026-09-28T1{i % 10}:00:00+00:00")
                for i in range(12)]]
    page, _accounts = _page(qapp, batches)
    page.on_shown()
    _pump()
    assert page.model.rowCount() == 12
    page.timeline.resize(900, 400)

    class _Settings:
        def __init__(self) -> None:
            self.saved: dict = {}

        def get(self, key):
            return self.saved.get(key, 176)

        def set(self, key, value):
            self.saved[key] = value

    page._settings = _Settings()
    page.timeline.resize(900, 400)
    qapp.processEvents()
    cols_before = page.timeline.timeline_model._cols

    page.tile_slider.setValue(120)                 # меньше плитка — больше колонок
    _pump(400)
    page.timeline.resize(901, 400)                 # детерминированный пересчёт
    qapp.processEvents()
    assert page.timeline.tile == 120
    assert page.timeline.timeline_model._cols > cols_before
    assert page._settings.saved["photo_tile"] == 120

    page.tile_slider.setValue(1000)                # ограничение сверху
    _pump(400)
    assert page.timeline.tile == TILE_MAX

    hidden = [ix for row in range(page.timeline.timeline_model.rowCount())
              for col in range(page.timeline.timeline_model._cols)
              if (ix := page.timeline.timeline_model.index(row, col)).data(ResourceRole)
              and page.timeline.isIndexHidden(ix)]
    assert not hidden
    assert TILE_MIN <= page.timeline.tile <= TILE_MAX


def test_open_photo_single_viewer_at_a_time(qapp, monkeypatch):
    """Пока просмотрщик открыт, повторные клики не плодят новые окна."""
    from PySide6.QtWidgets import QDialog

    import ydx.ui.views.photos_page as pp

    created = []

    class _Fake(QDialog):
        def __init__(self, *a, **k):
            super().__init__()
            created.append(self)
            self.exec = lambda: None          # без модального цикла в тесте

    monkeypatch.setattr(pp, "PhotoViewer", _Fake)
    monkeypatch.setattr(pp, "VideoViewer", _Fake)
    page, _accounts = _page(qapp, [])
    page.on_shown()
    _pump()
    item = _img("disk:/a.jpg", "2024-01-01T00:00:00+00:00")
    page.model.set_items([item])

    page._open_photo(item)
    assert len(created) == 1 and page._viewer_open
    page._open_photo(item)                    # второй клик — мимо
    assert len(created) == 1

    created[0].finished.emit(0)               # просмотрщик закрылся
    assert not page._viewer_open
    page._open_photo(item)
    assert len(created) == 2


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
    assert page.model.rowCount() == 1                            # лента пересобрана
    assert accounts.resources.generators == 1                    # из дискового кэша


def test_rest_feed_cache_served_without_rewalk(qapp):
    """Повторное открытие: REST-лента мгновенно из дискового кэша; ревизия
    Диска не менялась — обхода дерева нет; изменилась — обход и обновление."""
    batches = [[_img("disk:/Camera Uploads/a.jpg", "2026-10-01T10:00:00+00:00"),
                _img("disk:/Camera Uploads/b.jpg", "2026-10-02T10:00:00+00:00")]]
    page, _accounts = _page(qapp, batches)
    page.on_shown()
    _pump()
    assert page.model.rowCount() == 2
    assert page._accounts.resources.generators == 1       # был полный обход

    page2, _acc2 = _page(qapp, [])                        # «перезапуск» приложения
    page2.on_shown()
    _pump()
    assert page2.model.rowCount() == 2                    # мгновенно из кэша
    assert page2._accounts.resources.generators == 0      # обхода не было
    assert page2._accounts.resources.revision_calls == 1  # только проверка ревизии

    api = page2._accounts.resources
    api._rev = 8                                          # на Диске что-то поменялось
    api.batches = [[_img("disk:/Camera Uploads/a.jpg", "2026-10-01T10:00:00+00:00"),
                    _img("disk:/Camera Uploads/c.jpg", "2026-10-03T10:00:00+00:00")]]
    page2._collected.clear()
    page2.on_shown()
    _pump()
    assert page2.model.rowCount() == 3                    # a, b из кэша + c из обхода
    assert api.generators == 1


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


def test_video_badge_painted_on_tile(qapp):
    """Бейдж ▶ на плитке видео: белая стрелка в правом верхнем углу."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter

    from ydx.ui.views.photo_timeline import _TileDelegate

    img = QImage(60, 60, QImage.Format.Format_ARGB32)
    img.fill(0xFF101010)
    painter = QPainter(img)
    _TileDelegate._paint_video_badge(painter, QRect(0, 0, 60, 60))
    painter.end()

    center = img.pixelColor(35, 20)          # центр треугольника
    corner = img.pixelColor(5, 50)           # далеко от бейджа — фон
    assert center.red() > 180 and center.green() > 180 and center.blue() > 180
    assert corner.red() < 40
