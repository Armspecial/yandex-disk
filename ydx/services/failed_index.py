"""Индекс файлов с ошибкой: красный ✖ в списке, пока проблема не ушла.

Питается от неудачных передач и файловых операций; очищается при успехе
той же задачи и вручную из окна ошибок. Живёт в памяти сессии.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class FailedIndex(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._items: dict[str, str] = {}   # remote_path → причина

    def mark(self, remote_path: str, reason: str) -> None:
        self._items[remote_path] = (reason or "")[:200]
        self.changed.emit()

    def discard(self, remote_path: str) -> None:
        if remote_path in self._items:
            del self._items[remote_path]
            self.changed.emit()

    def clear(self) -> None:
        if self._items:
            self._items.clear()
            self.changed.emit()

    def has(self, remote_path: str) -> bool:
        return remote_path in self._items

    def reason(self, remote_path: str) -> str:
        return self._items.get(remote_path, "")

    @property
    def paths(self) -> list[str]:
        return list(self._items)
