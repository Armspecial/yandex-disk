"""Общий расчёт состояний файлов (offline/cloud/error) для всех разделов.

Используется страницами Файлы, Фото, Недавние, Общие: единые значки везде.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ydx.core.models import Resource


class StateService(QObject):
    states_changed = Signal()          # скачали/удалили/ошибка — пересчитать

    def __init__(self, downloaded=None, failed=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._downloaded = downloaded
        self._failed = failed
        if downloaded is not None:
            downloaded.index_changed.connect(self.states_changed)
        if failed is not None:
            failed.changed.connect(self.states_changed)

    def compute(self, resources: list[Resource]) -> dict[str, str]:
        """→ states: путь→'error'|'offline'|'cloud'.

        Папки, внутри которых есть файл с ошибкой, тоже получают 'error'.
        """
        downloaded = self._downloaded
        failed = self._failed
        failed_paths = failed.paths if failed is not None else []
        states: dict[str, str] = {}
        for r in resources:
            if failed is not None and failed.has(r.path):
                states[r.path] = "error"
            elif downloaded is not None and downloaded.has(r.path):
                states[r.path] = "offline"
            else:
                states[r.path] = "cloud"
        for r in resources:
            if not r.is_dir:
                continue
            prefix = r.path.rstrip("/") + "/"
            if any(fp.startswith(prefix) for fp in failed_paths):
                states[r.path] = "error"
        return states
