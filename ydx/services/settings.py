"""Обёртка над QSettings с типизированными ключами и значениями по умолчанию."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from ydx.constants import SETTINGS_FILE

DEFAULTS = {
    "theme": "system",            # system | light | dark
    "language": "ru",             # ru | en
    "client_id": "",
    "client_secret": "",
    "accounts": "",               # JSON-список аккаунтов (см. services/accounts)
    "active_account": "",
    "view_mode": "list",          # list | grid
    "sort": "modified",           # name | size | modified (+префикс -)
    "open_size_limit": 500 << 20,
    "proxy_host": "",
    "proxy_port": 0,
    "proxy_login": "",
    "proxy_password": "",
    "downloads_dir": "",          # пусто = системная папка «Загрузки»
}

_BOOL = {"true", "false"}


class AppSettings:
    def __init__(self) -> None:
        self._s = QSettings(str(SETTINGS_FILE), QSettings.IniFormat)

    def get(self, key: str):
        default = DEFAULTS[key]
        val = self._s.value(key, default)
        if isinstance(default, bool):
            return str(val).lower() in ("true", "1")
        if isinstance(default, int) and not isinstance(default, bool):
            try:
                return int(val)
            except (TypeError, ValueError):
                return default
        if isinstance(default, str) and isinstance(val, (bytes, bytearray)):
            return val.decode("utf-8", "replace")
        return val

    def set(self, key: str, value) -> None:
        self._s.setValue(key, value)
        self._s.sync()

    # Часто используемые — короткие свойства
    @property
    def theme(self) -> str:
        return self.get("theme")

    @property
    def language(self) -> str:
        return self.get("language")

    @property
    def client_id(self) -> str:
        return self.get("client_id")

    @property
    def client_secret(self) -> str:
        return self.get("client_secret")
