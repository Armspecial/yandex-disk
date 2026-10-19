"""Общий расчёт состояний файлов (offline/synced/cloud/error) для всех разделов.

Используется страницами Файлы, Фото, Недавние, Общие: единые значки везде.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ydx.core.models import Resource
from ydx.services.sync_state import LocalSyncIndex


class StateService(QObject):
    states_changed = Signal()          # скачали/удалили/ошибка — пересчитать

    def __init__(self, downloaded=None, failed=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._downloaded = downloaded
        self._failed = failed
        self.sync_index = LocalSyncIndex(self)
        if downloaded is not None:
            downloaded.index_changed.connect(self.states_changed)
        if failed is not None:
            failed.changed.connect(self.states_changed)

    def compute(self, resources: list[Resource], cloud_dir: str | None = None):
        """→ (states: путь→'error'|'offline'|'synced'|'cloud', local_only).

        Папки, внутри которых есть файл с ошибкой, тоже получают 'error'.
        local_only считается только при заданном cloud_dir (страница Файлы).
        """
        downloaded = self._downloaded
        failed = self._failed
        sync_on = self.sync_index.is_configured()
        failed_paths = failed.paths if failed is not None else []
        states: dict[str, str] = {}
        for r in resources:
            if failed is not None and failed.has(r.path):
                states[r.path] = "error"
            elif downloaded is not None and downloaded.has(r.path):
                states[r.path] = "offline"
            elif sync_on and self.sync_index.exists_locally(r.path):
                states[r.path] = "synced"
            else:
                states[r.path] = "cloud"
        for r in resources:
            if not r.is_dir:
                continue
            prefix = r.path.rstrip("/") + "/"
            if any(fp.startswith(prefix) for fp in failed_paths):
                states[r.path] = "error"

        local_only: list[tuple[str, bool]] = []
        if cloud_dir and sync_on:
            cloud_names = {r.name for r in resources}
            local_only = [
                (name, is_dir)
                for name, is_dir in self.sync_index.local_only_for(cloud_dir)
                if name not in cloud_names
            ]
        return states, local_only
