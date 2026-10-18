"""Запуск блокирующихся функций core в пуле потоков Qt.

run_in_thread(fn, on_done, on_error) — fire-and-forget с доставкой результата
в GUI-поток через сигналы. Живучесть relay-объектов обеспечивает реестр.
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Relay(QObject):
    done = Signal(object)
    error = Signal(object)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], relay: _Relay) -> None:
        super().__init__()
        self._fn = fn
        self._relay = relay
        self.setAutoDelete(True)

    def run(self) -> None:  # пул потоков Qt
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 — ретранслируем любую
            self._relay.error.emit(exc)
        else:
            self._relay.done.emit(result)


_alive: set[_Relay] = set()


def run_in_thread(
    fn: Callable[[], Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
) -> _Relay:
    relay = _Relay()
    if on_done is not None:
        relay.done.connect(on_done)
    if on_error is not None:
        relay.error.connect(on_error)

    def _cleanup(*_a: object) -> None:
        _alive.discard(relay)

    relay.done.connect(_cleanup)
    relay.error.connect(_cleanup)
    _alive.add(relay)

    QThreadPool.globalInstance().start(_Task(fn, relay))
    return relay
