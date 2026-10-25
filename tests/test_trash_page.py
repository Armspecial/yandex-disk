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
