"""TaskRow: скорость и ETA в строке работающей задачи."""

from __future__ import annotations

import os

import pytest


@pytest.fixture()
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _row(qapp, **overrides):
    from ydx.core.models import TransferTask
    from ydx.services import i18n
    from ydx.ui.views.transfers_view import TaskRow

    i18n.load("ru")
    fields = dict(id=1, kind="download", remote_path="disk:/видео/клип.mp4",
                  local_path="/tmp/клип.mp4", size=10 * 1024 * 1024)
    fields.update(overrides)
    return TaskRow(TransferTask(**fields))


def test_running_row_shows_speed_and_eta(qapp):
    row = _row(qapp, state="running", transferred=2 * 1024 * 1024,
               speed_bps=1024 * 1024)
    text = row.status_label.text()
    assert "/с" in text          # скорость
    assert "осталось" in text    # ETA


def test_running_row_without_speed_shows_plain_progress(qapp):
    row = _row(qapp, state="running", transferred=1024, speed_bps=0.0)
    assert "из" in row.status_label.text()
    assert "/с" not in row.status_label.text()


def test_done_row_shows_size_only(qapp):
    row = _row(qapp, state="done", transferred=10 * 1024 * 1024)
    assert row.status_label.text() == "10 МБ"


def test_paused_row_shows_state(qapp):
    row = _row(qapp, state="paused", transferred=1024)
    assert row.status_label.text() == "Пауза"
