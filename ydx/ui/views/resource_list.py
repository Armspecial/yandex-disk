"""Универсальная модель списка ресурсов (корзина, общие, недавние, фото).

Отличается от FilesModel: нет собственного пути/сортировки, элементы задаются
пачкой; опциональная ленивая догрузка через fetcher (offset, limit) → (items, next_offset).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSortFilterProxyModel, Qt

from ydx.core.models import Resource
from ydx.ui.views.files_model import ResourceRole

Fetcher = Callable[[int, int], tuple[list[Resource], int | None]]   # offset, limit → items, next_offset


class ResourceSortProxy(QSortFilterProxyModel):
    """Сортировка списка ресурсов на клиенте (дата/размер/имя)."""

    MODIFIED, SIZE, NAME = "modified", "size", "name"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.sort_key = self.NAME
        self.setDynamicSortFilter(True)
        self.setSortRole(Qt.ItemDataRole.UserRole)   # фактическую работу делает lessThan

    def set_sort_key(self, key: str) -> None:
        self.sort_key = key
        self.sort(0, Qt.SortOrder.DescendingOrder if key != self.NAME else Qt.SortOrder.AscendingOrder)

    def lessThan(self, left, right) -> bool:  # noqa: N802 — Qt API
        a = left.data(ResourceRole)
        b = right.data(ResourceRole)
        if a is None or b is None:
            return False
        if self.sort_key == self.MODIFIED:
            ka = a.modified.timestamp() if a.modified else 0
            kb = b.modified.timestamp() if b.modified else 0
            return ka < kb
        if self.sort_key == self.SIZE:
            return a.size < b.size
        return (a.name or "").lower() < (b.name or "").lower()


class ResourceListModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: list[Resource] = []
        self._fetcher: Fetcher | None = None
        self._next_offset: int | None = None
        self._loading = False
        self.total_loaded = 0

    # --- наполнение --------------------------------------------------------------

    def set_items(self, items: list[Resource]) -> None:
        self.beginResetModel()
        self._items = list(items)
        self._next_offset = None
        self._fetcher = None
        self.endResetModel()

    def set_fetcher(self, fetcher: Fetcher) -> None:
        """Ленивая модель: первая партия грузится fetchMore."""
        self.beginResetModel()
        self._items = []
        self._fetcher = fetcher
        self._next_offset = 0
        self.endResetModel()
        self.fetchMore()

    def reset(self) -> None:
        if self._fetcher is not None:
            fetcher = self._fetcher
            self.beginResetModel()
            self._items = []
            self._next_offset = 0
            self.endResetModel()
            self._fetcher = fetcher
            self.fetchMore()

    # --- пагинация -------------------------------------------------------------------

    def canFetchMore(self, parent: QModelIndex | None = None) -> bool:  # noqa: N802
        return self._next_offset is not None and not self._loading

    def fetchMore(self, parent: QModelIndex | None = None) -> None:  # noqa: N802
        if not self.canFetchMore(parent) or self._fetcher is None:
            return
        self._loading = True
        offset = self._next_offset or 0
        fetcher = self._fetcher

        def done(res: Any) -> None:
            items, next_offset = res
            if fetcher is not self._fetcher:
                return   # модель уже переустановлена
            self._next_offset = next_offset
            if items:
                row = len(self._items)
                self.beginInsertRows(QModelIndex(), row, row + len(items) - 1)
                self._items.extend(items)
                self.endInsertRows()
            elif next_offset is not None:
                self._next_offset = None
            self._loading = False

        from ydx.services.workers import run_in_thread

        run_in_thread(lambda: fetcher(offset, 200), done)

    # --- модель -------------------------------------------------------------------------

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 0 if parent and parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._items[index.row()].name
        if role == ResourceRole:
            return self._items[index.row()]
        return None

    # --- удобное --------------------------------------------------------------------------

    def resource_at(self, row: int) -> Resource | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    @property
    def items(self) -> list[Resource]:
        return list(self._items)

    @property
    def loading(self) -> bool:
        return self._loading
