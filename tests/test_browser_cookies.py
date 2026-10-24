"""Поиск кук Session_id в профилях браузеров (Chromium-шифрование, Firefox)."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path


def _encrypt(password: str, plain: bytes) -> bytes:
    """Как Chromium: PBKDF2 → AES-128-CBC, префикс v10."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1, 16)
    pad = 16 - len(plain) % 16
    plain = plain + bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.CBC(b" " * 16)).encryptor()
    return b"v10" + encryptor.update(plain) + encryptor.finalize()


def _future_expires() -> int:
    return int((time.time() + 3600 + 11_644_473_600) * 1_000_000)


def _chromium_db(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
        "encrypted_value BLOB, expires_utc INTEGER)"
    )
    con.executemany("INSERT INTO cookies VALUES (?,?,?,?,?)", rows)
    con.commit()
    con.close()


def _firefox_db(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT)")
    con.executemany("INSERT INTO moz_cookies VALUES (?,?,?)", rows)
    con.commit()
    con.close()


def _keyring(monkeypatch, entries: dict):
    from ydx.core import browser_cookies

    monkeypatch.setattr(browser_cookies, "_keyring_passwords", lambda: entries)


def test_chromium_encrypted_session_id(tmp_path, monkeypatch):
    from ydx.core.browser_cookies import find_cookie_header

    _keyring(monkeypatch, {"chromium": "test-key"})
    blob = _encrypt("test-key", b"3:abcdefsecret")
    _chromium_db(tmp_path / ".config/yandex-browser/Default/Cookies", [
        (".yandex.ru", "Session_id", "", blob, _future_expires()),
        (".yandex.ru", "yandexuid", "777", b"", _future_expires()),
        ("example.com", "other", "x", b"", _future_expires()),   # не яндекс — мимо
    ])

    found = find_cookie_header(tmp_path)
    assert found is not None
    browser, header = found
    assert browser == "Yandex Browser"
    assert "Session_id=3:abcdefsecret" in header
    assert "yandexuid=777" in header
    assert "other=" not in header


def test_chromium_digest_prefix_variant(tmp_path, monkeypatch):
    """v11 с 32-байтным digest-префиксом — текст берётся после префикса."""
    from ydx.core.browser_cookies import find_cookie_header

    _keyring(monkeypatch, {"yandex-browser": "k2"})
    blob = b"v11" + _encrypt("k2", b"\x01" * 32 + b"9:digestcase")[3:]
    _chromium_db(tmp_path / ".config/yandex-browser/Default/Cookies", [
        (".yandex.ru", "Session_id", "", blob, _future_expires()),
    ])
    _, header = find_cookie_header(tmp_path)
    assert "Session_id=9:digestcase" in header


def test_firefox_fallback(tmp_path, monkeypatch):
    from ydx.core.browser_cookies import find_cookie_header

    _keyring(monkeypatch, {})
    _firefox_db(tmp_path / ".mozilla/firefox/abcd.default-release/cookies.sqlite", [
        (".yandex.ru", "Session_id", "1:plainff"),
        (".yandex.ru", "my", "c", ),
    ])
    found = find_cookie_header(tmp_path)
    assert found is not None
    browser, header = found
    assert browser == "Firefox"
    assert "Session_id=1:plainff" in header


def test_priority_yandex_browser_first(tmp_path, monkeypatch):
    from ydx.core.browser_cookies import find_cookie_header

    _keyring(monkeypatch, {"chromium": "test-key"})
    blob = _encrypt("test-key", b"1:ya")
    _chromium_db(tmp_path / ".config/yandex-browser/Default/Cookies", [
        (".yandex.ru", "Session_id", "", blob, _future_expires()),
    ])
    _firefox_db(tmp_path / ".mozilla/firefox/x.default/cookies.sqlite", [
        (".yandex.ru", "Session_id", "1:ff"),
    ])
    _browser, header = find_cookie_header(tmp_path)
    assert "1:ya" in header and "1:ff" not in header


def test_no_session_id_returns_none(tmp_path, monkeypatch):
    from ydx.core.browser_cookies import find_cookie_header

    _keyring(monkeypatch, {})
    _chromium_db(tmp_path / ".config/google-chrome/Default/Cookies", [
        (".yandex.ru", "yandexuid", "777", b"", _future_expires()),
    ])
    assert find_cookie_header(tmp_path) is None


def test_wrong_keyring_password_skipped(tmp_path, monkeypatch):
    """Неправильный keyring-пароль расшифровку не проходит — браузер пропущен."""
    from ydx.core.browser_cookies import find_cookie_header

    _keyring(monkeypatch, {"chromium": "wrong"})
    blob = _encrypt("right", b"1:secret")
    _chromium_db(tmp_path / ".config/yandex-browser/Default/Cookies", [
        (".yandex.ru", "Session_id", "", blob, _future_expires()),
    ])
    assert find_cookie_header(tmp_path) is None


def test_expired_cookie_ignored(tmp_path, monkeypatch):
    from ydx.core.browser_cookies import find_cookie_header

    _keyring(monkeypatch, {})
    past = int((time.time() - 3600 + 11_644_473_600) * 1_000_000)
    _chromium_db(tmp_path / ".config/yandex-browser/Default/Cookies", [
        (".yandex.ru", "Session_id", "stale", b"", past),
    ])
    assert find_cookie_header(tmp_path) is None
