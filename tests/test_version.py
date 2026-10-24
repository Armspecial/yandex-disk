"""full_version: ревизия пакета из resources/VERSION (сборка .deb)."""

from __future__ import annotations

from ydx import __version__, full_version


def test_dev_without_version_file(tmp_path):
    assert full_version(tmp_path) == __version__      # dev-запуск: просто 0.1.0


def test_deb_with_version_file(tmp_path):
    (tmp_path / "VERSION").write_text("8", "utf-8")
    assert full_version(tmp_path) == f"{__version__}-8"


def test_empty_version_file(tmp_path):
    (tmp_path / "VERSION").write_text("  \n", "utf-8")
    assert full_version(tmp_path) == __version__      # без хвостового дефиса
