"""Куки Session_id из профилей локальных браузеров (Chromium-семейство, Firefox).

Yandex Browser/Chrome/Chromium шифруют базу кук ключом из системного keyring
(«… Safe Storage»): PBKDF2(SHA1, пароль, «saltysalt», 1 итерация) → AES-128-CBC,
префикс значения v10/v11 (у свежих движков после расшифровки бывает 32-байтный
digest-префикс). Firefox хранит куки открытым текстом в sqlite.

Читаем ТОЛЬКО куки яндексовых доменов — для ленты безлимитной автозагрузки.
База может быть заблокирована запущенным браузером — работаем с копией файла.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import sqlite3
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# приоритет: сначала браузер пользователя, потом остальные Chromium-семейства
CHROMIUM_DIRS = [
    ("Yandex Browser", "yandex-browser"),
    ("Google Chrome", "google-chrome"),
    ("Chromium", "chromium"),
    ("Brave", "brave-browser"),
    ("Vivaldi", "vivaldi"),
]
FIREFOX_DIR = ".mozilla/firefox"

# host, под который Яндекс ставит авторизационные куки
_HOST_SQL = ("host_key LIKE '%.yandex.ru' OR host_key LIKE '%.yandex.com' "
             "OR host_key = 'yandex.ru' OR host_key = 'yandex.com'")
_HOST_SQL_FF = ("host LIKE '%.yandex.ru' OR host LIKE '%.yandex.com' "
                "OR host = 'yandex.ru' OR host = 'yandex.com'")

_CHROMIUM_EPOCH_DELTA = 11_644_473_600        # секунды между 1601 и 1970


def _keyring_passwords() -> dict[str, str]:
    """{имя приложения: пароль} из Secret Service (Chrome Safe Storage и пр.)."""
    found: dict[str, str] = {}
    try:
        import secretstorage

        bus = secretstorage.dbus_init()
        for collection in secretstorage.get_all_collections(bus):
            if collection.is_locked():
                continue
            for item in collection.get_all_items():
                attrs = item.get_attributes()
                app = str(attrs.get("application", "")).lower()
                label = item.get_label().lower()
                if "safe storage" in label and (app or label):
                    try:
                        found[app or label] = item.get_secret().decode("utf-8", "replace")
                    except Exception:  # noqa: BLE001 — битый секрет не должен ронять поиск
                        continue
    except Exception as exc:  # noqa: BLE001 — без keyring пробуем пустой пароль
        log.debug("keyring unavailable for cookie decrypt: %s", exc)
    return found


def _decrypt_chromium(blob: bytes, passwords: list[str]) -> str | None:
    """v10/v11 → открытый текст; None, если ни один ключ не подошёл."""
    if blob[:3] not in (b"v10", b"v11"):
        return None
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    for password in passwords:
        try:
            key = hashlib.pbkdf2_hmac(
                "sha1", password.encode("utf-8"), b"saltysalt", 1, 16
            )
            decryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).decryptor()
            plain = decryptor.update(blob[3:]) + decryptor.finalize()
            plain = plain[: -plain[-1]]              # PKCS7
            for candidate in (plain, plain[32:]):    # свежий движок: digest-префикс
                text = candidate.decode("utf-8", "replace")
                if text and all(ch.isprintable() for ch in text):
                    return text
        except Exception:  # noqa: BLE001 — неправильный ключ: пробуем следующий
            continue
    return None


def _chromium_cookies(cookies_db: Path) -> dict[str, str] | None:
    """{имя куки: значение} яндексовых доменов из одной базы Chromium."""
    tmp = Path(tempfile.mkstemp(suffix=".sqlite")[1])
    try:
        shutil.copy(cookies_db, tmp)
        con = sqlite3.connect(tmp)
        try:
            rows = con.execute(
                f"SELECT host_key, name, value, encrypted_value, expires_utc "
                f"FROM cookies WHERE {_HOST_SQL}"
            ).fetchall()
        finally:
            con.close()
    except (OSError, sqlite3.Error) as exc:
        log.debug("cookie db %s unreadable: %s", cookies_db, exc)
        return None
    finally:
        tmp.unlink(missing_ok=True)

    import time

    now_chromium = (time.time() + _CHROMIUM_EPOCH_DELTA) * 1_000_000
    passwords = list(_keyring_passwords().values()) + [""]
    best: dict[str, tuple[int, str]] = {}       # name → (expires, value)
    for _host, name, value, encrypted, expires in rows:
        if 0 < expires < now_chromium:
            continue                              # истекла
        text = value or (encrypted and _decrypt_chromium(encrypted, passwords))
        if not text:
            continue
        if name not in best or expires > best[name][0]:
            best[name] = (expires, text)
    return {name: value for name, (_expires, value) in best.items()} or None


def _firefox_cookies(cookies_db: Path) -> dict[str, str] | None:
    tmp = Path(tempfile.mkstemp(suffix=".sqlite")[1])
    try:
        shutil.copy(cookies_db, tmp)
        con = sqlite3.connect(tmp)
        try:
            rows = con.execute(
                f"SELECT host, name, value FROM moz_cookies WHERE {_HOST_SQL_FF}"
            ).fetchall()
        finally:
            con.close()
    except (OSError, sqlite3.Error) as exc:
        log.debug("cookie db %s unreadable: %s", cookies_db, exc)
        return None
    finally:
        tmp.unlink(missing_ok=True)

    cookies = {name: value for _host, name, value in rows if value}
    return cookies or None


def find_cookie_header(home: Path | None = None) -> tuple[str, str] | None:
    """Ищет Session_id по профилям браузеров → (имя браузера, строка Cookie).

    Порядок: Chromium-семейство по приоритету, затем Firefox.
    """
    config = (home or Path.home()) / ".config"
    for browser, dirname in CHROMIUM_DIRS:
        root = config / dirname
        if not root.is_dir():
            continue
        for cookies_db in sorted(root.glob("*/Cookies")):
            cookies = _chromium_cookies(cookies_db)
            if cookies and "Session_id" in cookies:
                return browser, "; ".join(f"{k}={v}" for k, v in cookies.items())
    profiles = (home or Path.home()) / FIREFOX_DIR
    if profiles.is_dir():
        for cookies_db in sorted(profiles.glob("*/cookies.sqlite")):
            cookies = _firefox_cookies(cookies_db)
            if cookies and "Session_id" in cookies:
                return "Firefox", "; ".join(f"{k}={v}" for k, v in cookies.items())
    return None
