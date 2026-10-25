"""Корзина: сортировка по дате удаления — свежее сверху."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture()
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _trashed(name: str, deleted_days_ago: float):
    from ydx.core.models import Resource

    return Resource(
        name=name, type="file", path=f"trash:/{name}",
        media_type="image", size=10,
        modified=datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        - timedelta(days=deleted_days_ago),
    )


def test_trash_sorted_by_deletion_date(qapp):
    """Страница заворачивает модель в прокси: первые строки — свежие."""
    from ydx.core.models import Resource as R
    from ydx.ui.views.resource_list import (
        ResourceListModel,
        ResourceSortProxy,
    )

    model = ResourceListModel()
    model.set_items([
        _trashed("old.jpg", 18),
        _trashed("новое.jpg", 0.1),          # только что удалённое
        _trashed("mid.jpg", 5),
    ])
    proxy = ResourceSortProxy()
    proxy.setSourceModel(model)
    proxy.set_sort_key(ResourceSortProxy.MODIFIED)

    names = [proxy.index(row, 0).data() for row in range(proxy.rowCount())]
    assert names == ["новое.jpg", "mid.jpg", "old.jpg"]


def test_trash_page_uses_modified_sort(qapp):
    """TrashPage подключает прокси с сортировкой по дате и живёт."""
    from PySide6.QtCore import QObject, Signal

    from ydx.services import i18n
    from ydx.ui.views.trash_page import TrashPage

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

    page = TrashPage(_Acc(), _Thumbs())
    assert page.sort_proxy.sort_key == page.sort_proxy.MODIFIED
    assert page.view.model() is page.sort_proxy
    page.on_shown()                      # ресурсов нет — просто не падаем


# --- мгновенное обновление по клику --------------------------------------------


def _pump(ms: int = 1500) -> None:
    import time

    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance()
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def test_fetcher_reset_means_loading_not_empty(qapp):
    """После set_fetcher модель сразу «грузит»: страница не мигает
    «Корзина пуста» между кликом и приходом списка."""
    from ydx.ui.views.resource_list import ResourceListModel

    model = ResourceListModel()
    model.set_fetcher(lambda off, lim: ([_trashed("a.jpg", 1)], None))
    assert model.loading                     # сразу, без ожидания потоков
    _pump()
    assert not model.loading
    assert [r.name for r in model.items] == ["a.jpg"]


def test_fetcher_empty_finish_emits_layout_changed(qapp):
    """Пустая корзина: финал без rowsInserted всё равно меняет состояние
    страницы (layoutChanged), «загрузка» не висит вечно."""
    from ydx.ui.views.resource_list import ResourceListModel

    fired = []
    model = ResourceListModel()
    model.layoutChanged.connect(lambda: fired.append(True))
    model.set_fetcher(lambda off, lim: ([], None))
    _pump()
    assert not model.loading
    assert fired                              # страница узнала о финале


def test_on_shown_resets_to_root(qapp):
    """Клик по «Корзине» в сайдбаре всегда показывает корень заново,
    даже если до этого зашли в подпапку."""
    from PySide6.QtCore import QObject, Signal

    from ydx.services import i18n
    from ydx.ui.views.trash_page import TrashPage

    i18n.load("ru")

    class _Thumbs(QObject):
        ready = Signal(str)

        def pixmap(self, *_a, **_k):
            return None

    class _Api:
        def trash_list(self, path="/", offset=0, limit=100):
            return ([], None)

    class _Acc:
        class _A:
            login = "u"

        active = _A()
        accounts = []
        resources = _Api()

    page = TrashPage(_Acc(), _Thumbs())
    page._path = "trash:/sub"
    page._history = ["/"]
    page.back_btn.setEnabled(True)
    _pump(100)

    page.on_shown()
    assert page._path == "/"
    assert not page._history
    assert not page.back_btn.isEnabled()
    _pump()
