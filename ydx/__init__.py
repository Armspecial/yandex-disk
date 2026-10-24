"""Яндекс.Диск для Linux — неофициальный настольный клиент на PySide6."""

from pathlib import Path

__version__ = "0.1.0"
APP_ID = "yandex-disk-linux"


def full_version(resources: Path | None = None) -> str:
    """Версия целиком: «0.1.0-8» в сборке .deb (файл resources/VERSION),
    «0.1.0» — в dev-запуске из репозитория (файла нет)."""
    directory = resources or Path(__file__).parent / "resources"
    try:
        build = (directory / "VERSION").read_text("utf-8").strip()
    except OSError:
        return __version__
    return f"{__version__}-{build}" if build else __version__
