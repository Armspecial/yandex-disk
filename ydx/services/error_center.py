"""Центр ошибок: собирает всё, что пошло не так, в одном месте."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QObject, Signal

MAX_ITEMS = 200


@dataclass
class AppError:
    source: str        # files | transfers | sync | auth
    message: str
    time: datetime = field(default_factory=datetime.now)

    @property
    def time_str(self) -> str:
        return self.time.strftime("%d.%m %H:%M")


class ErrorCenter(QObject):
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.errors: list[AppError] = []

    def report(self, source: str, message: str) -> None:
        message = (message or "").strip() or "—"
        # не дублируем подряд одинаковые (и не дёргаем сигнал)
        if self.errors and self.errors[0].source == source and self.errors[0].message == message:
            self.errors[0].time = datetime.now()
            return
        self.errors.insert(0, AppError(source=source, message=message))
        del self.errors[MAX_ITEMS:]
        self.changed.emit()

    def clear(self) -> None:
        self.errors.clear()
        self.changed.emit()

    @property
    def count(self) -> int:
        return len(self.errors)
