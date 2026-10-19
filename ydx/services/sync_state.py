"""Индекс локальной папки синхронизации: что уже на компьютере.

Сравнивает пути облака (disk:/a/b → a/b) с содержимым локальной папки
демона (кроме служебной .sync). Позволяет рисовать значки
«синхронизировано / только в облаке» и показывать файлы,
которые есть только на компьютере (ещё не выгружены).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QObject

from ydx.core.daemon_config import read_config
from ydx.constants import DAEMON_CONFIG_FILE

CACHE_TTL = 5.0   # секунд


class LocalSyncIndex(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dir: str | None = None
        self._files: set[str] = set()      # относительные пути файлов
        self._dirs: set[str] = set()       # относительные пути папок
        self._stamp = 0.0

    # --- конфиг и обновление ---------------------------------------------------

    @staticmethod
    def sync_dir() -> str | None:
        cfg = read_config(DAEMON_CONFIG_FILE)
        return cfg.get("dir") or None

    def _refresh_if_needed(self) -> None:
        directory = self.sync_dir()
        if directory != self._dir:
            self._dir = directory
            self._stamp = 0.0   # каталог сменился — переиндексировать
        if self._dir is None:
            return
        try:
            root = Path(self._dir)
            if time.monotonic() - self._stamp < CACHE_TTL:
                return
            files: set[str] = set()
            dirs: set[str] = set()
            for dirpath, dirnames, filenames in os.walk(root):
                rel_dir = Path(dirpath).relative_to(root).as_posix()
                if rel_dir == ".sync" or ".sync" in Path(dirpath).relative_to(root).parts:
                    dirnames[:] = []
                    continue
                if rel_dir != ".":
                    dirs.add(rel_dir)
                for name in dirnames:
                    if name != ".sync":
                        dirs.add(f"{rel_dir}/{name}" if rel_dir != "." else name)
                for name in filenames:
                    files.add(f"{rel_dir}/{name}" if rel_dir != "." else name)
            self._files, self._dirs = files, dirs
            self._stamp = time.monotonic()
        except OSError:
            self._files, self._dirs = set(), set()

    # --- запросы -----------------------------------------------------------------

    def is_configured(self) -> bool:
        return self.sync_dir() is not None

    def exists_locally(self, cloud_path: str) -> bool:
        """Есть ли копия ресурса disk:/a/b на компьютере."""
        rel = cloud_path.removeprefix("disk:/").lstrip("/")
        if not rel:
            return True   # корень синхронизации существует всегда
        self._refresh_if_needed()
        return rel in self._files or rel in self._dirs

    def local_only_for(self, cloud_dir: str) -> list[tuple[str, bool]]:
        """Записи локальной папки, соответствующей cloud_dir, которых нет в облаке.

        Возвращает [(имя, is_dir)] — для показа «ждёт выгрузки».
        """
        self._refresh_if_needed()
        if not self._dir:
            return []
        rel_dir = cloud_dir.removeprefix("disk:/").strip("/")
        prefix = f"{rel_dir}/" if rel_dir else ""
        result: list[tuple[str, bool]] = []
        seen_names: set[str] = set()
        for path in self._files:
            if not path.startswith(prefix):
                continue
            tail = path[len(prefix):]
            if "/" in tail:
                continue   # это внутри подпапки — сама подпапка посчитается
            result.append((tail, False))
            seen_names.add(tail)
        for path in self._dirs:
            if not path.startswith(prefix):
                continue
            tail = path[len(prefix):]
            if "/" in tail:
                continue
            result.append((tail, True))
        # дочерние папки local-only: показать и подпапку, если её нет в облаке
        return result
