"""Модель листинга папки: пагинация через canFetchMore/fetchMore.

Папки всегда впереди файлов — так сортирует сам API. Устаревшие ответы
отбрасываются по счётчику поколений (_gen).
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt

from ydx.constants import PAGE_SIZE
from ydx.core.api_resources import ResourcesApi
from ydx.core.models import Resource
from ydx.services.workers import run_in_thread

MIME_INTERNAL = "application/x-ydx-paths"

ResourceRole = Qt.ItemDataRole.UserRole + 1


class FilesModel(QAbstractListModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._resources: ResourcesApi | None = None
        self._items: list[Resource] = []
        self._total = 0
        self._loading = False
        self._path = "disk:/"
        self._sort: str | None = "name"
        self._gen = 0

    # --- API -------------------------------------------------------------------

    def set_api(self, resources: ResourcesApi | None) -> None:
        self._resources = resources
        self._gen += 1
        self.beginResetModel()
        self._items, self._total, self._loading = [], 0, False
        self.endResetModel()

    @property
    def path(self) -> str:
        return self._path

    @property
    def total(self) -> int:
        return self._total

    @property
    def loading(self) -> bool:
        return self._loading

    def load(self, path: str, sort: str | None = None) -> None:
        """(Пере)загрузить папку с нуля."""
        if sort is not None:
            self._sort = sort
        self._path = path
        self._gen += 1
        gen = self._gen
        self.beginResetModel()
        self._items, self._total, self._loading = [], 0, True
        self.endResetModel()
        self._fetch_page(0, gen)

    # --- пагинация ----------------------------------------------------------------

    def canFetchMore(self, parent: QModelIndex | None = None) -> bool:  # noqa: N802
        return (
            not self._loading
            and self._resources is not None
            and self._total > len(self._items)
        )

    def fetchMore(self, parent: QModelIndex | None = None) -> None:  # noqa: N802
        if not self.canFetchMore(parent):
            return
        self._loading = True
        self._fetch_page(len(self._items), self._gen)

    def _fetch_page(self, offset: int, gen: int) -> None:
        if self._resources is None:
            return

        def job():
            return self._resources.list_page(
                self._path, offset=offset, limit=PAGE_SIZE, sort=self._sort
            )

        run_in_thread(job, lambda res: self._on_page(gen, offset, res))

    def _on_page(self, gen: int, offset: int, res: tuple) -> None:
        items, total = res
        if gen != self._gen or not items:
            return
        self._total = total if total is not None else offset + len(items)
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row + len(items) - 1)
        self._items.extend(items)
        self.endInsertRows()
        self._loading = False

    # --- QAbstractListModel ----------------------------------------------------------

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 0 if parent and parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        resource = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return resource.name
        if role == ResourceRole:
            return resource
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.isValid():
            base |= Qt.ItemFlag.ItemIsDragEnabled
            if self._items[index.row()].is_dir:
                base |= Qt.ItemFlag.ItemIsDropEnabled
        return base

    def mimeTypes(self) -> list[str]:
        return [MIME_INTERNAL]

    def mimeData(self, indexes: list[QModelIndex]) -> "QMimeData | None":
        from PySide6.QtCore import QMimeData

        paths = [
            self._items[i.row()].path for i in indexes if i.isValid()
        ]
        if not paths:
            return None
        mime = QMimeData()
        mime.setData(MIME_INTERNAL, "\n".join(paths).encode("utf-8"))
        mime.setText("\n".join(paths))
        return mime

    # --- удобное --------------------------------------------------------------------

    def resource_at(self, row: int) -> Resource | None:
        return self._items[row] if 0 <= row < len(self._items) else None

    def row_of(self, path: str) -> int:
        for i, r in enumerate(self._items):
            if r.path == path:
                return i
        return -1
