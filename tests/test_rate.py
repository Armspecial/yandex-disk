"""Speedometer (окна ≥0.5 с) и форматирование ETA/суммарных скоростей."""

from __future__ import annotations


class _Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_speedometer_first_update_baselines():
    from ydx.core.rate import Speedometer

    meter = Speedometer(_Clock())
    assert meter.update(1000) == 0.0   # первый вызов — только базовая точка
    assert meter.speed == 0.0


def test_speedometer_window():
    from ydx.core.rate import Speedometer

    clock = _Clock()
    meter = Speedometer(clock)
    meter.update(0)
    clock.advance(0.4)
    assert meter.update(400) == 0.0    # окно ещё не прошло
    clock.advance(0.6)
    assert meter.update(500) == 500.0  # 500 байт за 1.0 с
    assert meter.speed == 500.0


def test_speedometer_reset():
    from ydx.core.rate import Speedometer

    clock = _Clock()
    meter = Speedometer(clock)
    meter.update(0)
    clock.advance(1.0)
    meter.update(500)
    meter.reset()
    assert meter.speed == 0.0
    clock.advance(1.0)
    assert meter.update(2000) == 0.0   # заново базируется


def test_human_eta_ru():
    from ydx.services import i18n
    from ydx.services.fmt import human_eta

    i18n.load("ru")
    assert human_eta(None) == "—"
    assert human_eta(-1) == "—"
    assert human_eta(44.6) == "45 с"
    assert human_eta(60) == "1 мин"
    assert human_eta(72) == "1 мин 12 с"
    assert human_eta(3700) == "1 ч 01 мин"
    assert human_eta(90000) == "1 дн 01 ч"


def test_human_eta_en():
    from ydx.services import i18n
    from ydx.services.fmt import human_eta

    i18n.load("en")
    try:
        assert human_eta(45) == "45 s"
        assert human_eta(60) == "1m"
        assert human_eta(72) == "1m 12s"
        assert human_eta(3700) == "1h 01m"
    finally:
        i18n.load("ru")   # не оставляем смену языка другим тестам


def test_transfer_speeds_text():
    from ydx.services import i18n
    from ydx.services.fmt import transfer_speeds_text

    i18n.load("ru")
    assert transfer_speeds_text(0, 0) == ""
    assert transfer_speeds_text(1024, 0) == "↓ 1 КБ/с"
    assert transfer_speeds_text(0, 2048) == "↑ 2 КБ/с"
    assert transfer_speeds_text(1024, 2048) == "↓ 1 КБ/с · ↑ 2 КБ/с"
