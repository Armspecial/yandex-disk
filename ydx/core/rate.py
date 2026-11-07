"""Измеритель скорости передачи (EMA по полусекундным окнам)."""

from __future__ import annotations

import time
from typing import Callable


class Speedometer:
    """Скорость загрузки/выгрузки: пересчёт не чаще раза в 0.5 с."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._t0: float | None = None
        self._done0 = 0
        self.speed = 0.0                     # байт/с

    def update(self, done: int) -> float:
        now = self._clock()
        if self._t0 is None:
            self._t0 = now
            self._done0 = done
            return self.speed
        dt = now - self._t0
        if dt >= 0.5:
            self.speed = max(0.0, (done - self._done0) / dt)
            self._t0 = now
            self._done0 = done
        return self.speed

    def reset(self) -> None:
        self._t0 = None
        self._done0 = 0
        self.speed = 0.0
