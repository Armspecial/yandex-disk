"""Хранение OAuth-токенов: GNOME Keyring (libsecret) с файловым fallback.

Файловый fallback — JSON с правами 0600 в ~/.config/yandex-disk-linux/;
используется, когда секретсервис недоступен (SSH-сессия, отсутствие dbus).
UI обязано показывать предупреждение при secure=False.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from ydx import constants as const
from ydx.constants import APP_ID

log = logging.getLogger(__name__)


class TokenStore:
    def __init__(self) -> None:
        self._use_keyring = False
        try:
            import keyring  # noqa: F401 — проверка доступности бэкенда

            keyring.get_keyring()
            self._use_keyring = True
        except Exception as exc:  # pragma: no cover - зависит от окружения
            log.warning("keyring недоступен (%s), токены пойдут в файл 0600", exc)
        # ссылка через модуль — чтобы тесты могли подменять каталог (monkeypatch)
        self._file = const.CONFIG_DIR / "tokens.json"

    @property
    def secure(self) -> bool:
        return self._use_keyring

    # --- API ------------------------------------------------------------------

    def save(self, account_key: str, tokens: dict[str, Any]) -> None:
        if self._use_keyring:
            import keyring

            keyring.set_password(APP_ID, account_key, json.dumps(tokens))
        else:
            data = self._load_file()
            data[account_key] = tokens
            self._write_file(data)

    def load(self, account_key: str) -> dict[str, Any] | None:
        if self._use_keyring:
            import keyring

            raw = keyring.get_password(APP_ID, account_key)
            if raw is None:
                return None
            try:
                return json.loads(raw)
            except ValueError:
                return None
        return self._load_file().get(account_key)

    def delete(self, account_key: str) -> None:
        if self._use_keyring:
            import keyring

            try:
                keyring.delete_password(APP_ID, account_key)
            except Exception:
                pass
        else:
            data = self._load_file()
            data.pop(account_key, None)
            self._write_file(data)

    # --- файловый fallback ---------------------------------------------------

    def _load_file(self) -> dict[str, Any]:
        try:
            return json.loads(self._file.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def _write_file(self, data: dict[str, Any]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.chmod(0o600)
        tmp.replace(self._file)
