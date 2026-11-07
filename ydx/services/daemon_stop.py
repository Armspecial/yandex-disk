"""Разовый best-effort стоп осиротевшего демона yandex-disk.

Синхронизации в приложении больше нет; если старая версия оставила
демон работать (наш конфиг ~/.config/yandex-disk-linux/daemon.cfg),
останавливаем его один раз при запуске. Чужой демон (официальный,
~/.config/yandex-disk/config.cfg) не трогаем.
"""

from __future__ import annotations

import shutil

from ydx.constants import DAEMON_BINARY, DAEMON_CONFIG_FILE


def stop_leftover_daemon() -> bool:
    """Остановить забытый демон, если бинарник и наш конфиг на месте.

    Ошибки молча проглатываются: это гигиена, а не функциональность.
    """
    try:
        binary = shutil.which(DAEMON_BINARY)
        if binary is None or not DAEMON_CONFIG_FILE.exists():
            return False
        from PySide6.QtCore import QProcess

        return QProcess.startDetached(binary, ["stop", "-c", str(DAEMON_CONFIG_FILE)])
    except Exception:
        return False
