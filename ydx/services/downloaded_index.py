"""Скачанные через приложение файлы: индекс «доступно офлайн» + слежение.

Файл, скачанный действием «Скачать», помечается значком «есть на компьютере».
Если пользователь меняет такой файл локально — перезаливаем его в облако
(QFileSystemWatcher + дебаунс). Файл удалили локально — значок снимается.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QFileSystemWatcher, Signal

from ydx.constants import CONFIG_DIR


class DownloadedIndex(QObject):
    file_changed = Signal(str, str)      # remote_path, local_path — перезалить
    index_changed = Signal()             # значки обновить

    DEBOUNCE_MS = 1200

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._file = CONFIG_DIR / "downloaded.json"
        self._data: dict[str, str] = {}      # remote_path → local_path
        self._load()
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._timers: dict[str, QTimer] = {}
        self._rewatch()

    # --- персистентность ---------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = json.loads(self._file.read_text("utf-8"))
            if isinstance(raw, dict):
                self._data = {
                    k: v for k, v in raw.items()
                    if isinstance(k, str) and isinstance(v, str)
                }
        except (OSError, ValueError):
            self._data = {}

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._file.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=1), "utf-8"
        )

    # --- API ------------------------------------------------------------------------

    def has(self, remote_path: str) -> bool:
        return remote_path in self._data

    def local_for(self, remote_path: str) -> str | None:
        return self._data.get(remote_path)

    def add(self, remote_path: str, local_path: str) -> None:
        self._data[remote_path] = local_path
        self._save()
        self._watch(local_path)
        self.index_changed.emit()

    def remove(self, remote_path: str) -> None:
        local = self._data.pop(remote_path, None)
        if local:
            timer = self._timers.pop(local, None)
            if timer:
                timer.stop()
        self._save()
        self.index_changed.emit()

    def entries(self) -> dict[str, str]:
        return dict(self._data)

    # --- слежение ----------------------------------------------------------------------

    def _rewatch(self) -> None:
        existing = set(self._watcher.files())
        wanted = {p for p in self._data.values() if Path(p).exists()}
        if existing != wanted:
            if existing:
                self._watcher.removePaths(list(existing))
            if wanted:
                self._watcher.addPaths(list(wanted))

    def _watch(self, local_path: str) -> None:
        if Path(local_path).exists() and local_path not in self._watcher.files():
            self._watcher.addPath(local_path)

    def _on_file_changed(self, local_path: str) -> None:
        # редакторы часто пишут через rename — путь может «отвалиться»
        if not Path(local_path).exists():
            self._rewatch()
            remote = next((k for k, v in self._data.items() if v == local_path), None)
            if remote:
                self.remove(remote)
            return
        self._watch(local_path)
        timer = self._timers.get(local_path)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda lp=local_path: self._emit_change(lp))
            self._timers[local_path] = timer
        timer.start(self.DEBOUNCE_MS)

    def _emit_change(self, local_path: str) -> None:
        if not Path(local_path).exists():
            return
        remote = next((k for k, v in self._data.items() if v == local_path), None)
        if remote:
            self.file_changed.emit(remote, local_path)
