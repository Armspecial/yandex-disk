"""Безлимитная автозагрузка: хранение кук + жизненный цикл клиента.

Куки — секрет уровня OAuth-токена (полный доступ к web-сессии), поэтому
лежат отдельным файлом 0600 в ~/.config/yandex-disk-linux/, по ключу логина.
TokenStore не используется: logout удаляет свои ключи целиком, а куки
должны переживать перелогин того же аккаунта.

Лента кэшируется в ~/.cache (обход — ~250 запросов с паузами, повторять
при каждом старте жалко): свежий кэш отдаётся мгновенно без сети,
устаревший — показывается сразу и обновляется фоном.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator

from PySide6.QtCore import QObject, Signal

from ydx import constants as const
from ydx.core.models import Resource
from ydx.core.unlim_api import UnlimClient, parse_cookie_string
from ydx.services.accounts import AccountManager
from ydx.services.settings import AppSettings

log = logging.getLogger(__name__)

FEED_TTL = 12 * 3600                # сколько кэш ленты считается свежим


def _resource_to_dict(r: Resource) -> dict:
    return {
        "name": r.name, "path": r.path, "size": r.size,
        "created": r.created.isoformat() if r.created else None,
        "modified": r.modified.isoformat() if r.modified else None,
        "mime_type": r.mime_type, "media_type": r.media_type,
        "preview": r.preview,
    }


def _resource_from_dict(d: dict) -> Resource:
    from datetime import datetime

    def _dt(value):
        try:
            return datetime.fromisoformat(value) if value else None
        except ValueError:
            return None

    return Resource(
        name=d.get("name") or "", type="file", path=d.get("path") or "",
        size=int(d.get("size") or 0),
        created=_dt(d.get("created")), modified=_dt(d.get("modified")),
        mime_type=d.get("mime_type"), media_type=d.get("media_type"),
        preview=d.get("preview"),
    )


class UnlimFeedCache:
    """Лента одного логина в ~/.cache: {saved_at, items[]}. Секретов нет."""

    def __init__(self, login: str) -> None:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", login)[:60] or "anon"
        self._file = const.CACHE_DIR / f"unlim_feed_{safe}.json"

    def load(self) -> tuple[float, list[Resource]] | None:
        try:
            data = json.loads(self._file.read_text("utf-8"))
            saved_at = float(data.get("saved_at") or 0)
            items = [_resource_from_dict(d) for d in data.get("items") or []]
        except (OSError, ValueError, TypeError):
            return None
        if not items:
            return None
        return saved_at, items

    def save(self, items: list[Resource]) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "saved_at": time.time(),
                "items": [_resource_to_dict(r) for r in items],
            }, ensure_ascii=False), "utf-8")
            tmp.replace(self._file)
        except OSError as exc:
            log.debug("unlim feed cache write failed: %s", exc)

    def clear(self) -> None:
        try:
            self._file.unlink(missing_ok=True)
        except OSError:
            pass


class UnlimCookieStore:
    """{login: {"cookie": "Session_id=…", "auto": bool}} в JSON 0600.

    auto=True — куки взяты из локального браузера автоматически (можно
    самообновлять при истечении); False — вставлены вручную, не трогаем.
    """

    def __init__(self) -> None:
        # ссылка через модуль — чтобы тесты могли подменять каталог (monkeypatch)
        self._file = const.CONFIG_DIR / "unlim_cookies.json"

    def save(self, login: str, cookies: str, auto: bool = False) -> None:
        data = self._load()
        data[login] = {"cookie": cookies, "auto": auto}
        self._write(data)

    def load(self, login: str) -> str | None:
        entry = self._load().get(login)
        if isinstance(entry, dict):
            return entry.get("cookie")
        return entry                              # старый формат: чистая строка

    def is_auto(self, login: str) -> bool:
        entry = self._load().get(login)
        return bool(isinstance(entry, dict) and entry.get("auto"))

    def delete(self, login: str) -> None:
        data = self._load()
        data.pop(login, None)
        self._write(data)

    def logins(self) -> list[str]:
        return list(self._load())

    def _load(self) -> dict:
        try:
            return json.loads(self._file.read_text("utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict[str, str]) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        tmp.chmod(0o600)
        tmp.replace(self._file)


class UnlimService(QObject):
    """Куки + клиент внутреннего API для активного аккаунта."""

    cookies_changed = Signal()

    def __init__(self, settings: AppSettings, accounts: AccountManager,
                 store: UnlimCookieStore | None = None, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._accounts = accounts
        self._store = store or UnlimCookieStore()
        self._client: UnlimClient | None = None
        self._client_login: str | None = None
        accounts.active_changed.connect(lambda _a: self._reset_client())
        accounts.accounts_changed.connect(lambda _l: self._prune())

    # --- состояние ------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Пользователь не выключил безлимит в настройках."""
        return bool(self._settings.get("show_unlim"))

    @property
    def has_cookies(self) -> bool:
        login = self._active_login()
        return bool(login and self._store.load(login))

    @property
    def client(self) -> UnlimClient | None:
        """Клиент для активного аккаунта (лениво, кэшируется)."""
        login = self._active_login()
        if login is None:
            return None
        if self._client is None or self._client_login != login:
            raw = self._store.load(login)
            self._client = UnlimClient(raw) if raw else None
            self._client_login = login if raw else None
        return self._client

    def _active_login(self) -> str | None:
        account = self._accounts.active
        return account.login if account is not None else None

    def _reset_client(self) -> None:
        self._client = None
        self._client_login = None

    def _prune(self) -> None:
        """Куки удалённых аккаунтов не должны оставаться в файле."""
        known = {a.login for a in self._accounts.accounts}
        stored = set(self._store.logins())
        for login in stored - known:
            self._store.delete(login)
            if login == self._client_login:
                self._reset_client()

    # --- действия -------------------------------------------------------------

    def check(self, raw: str) -> int:
        """Проверить вставленные куки: сколько элементов в безлимите."""
        return UnlimClient(raw).count()

    def check_stored(self) -> int:
        """Проверить СОХРАНЁННЫЕ куки по сети (для «Проверить» с пустым полем)."""
        client = self.client
        if client is None:
            raise RuntimeError("no cookies")
        return client.count()

    def download_links(self, resources: list[Resource]) -> dict[str, str]:
        """Свежие ссылки на оригиналы: {path ресурса: url}."""
        client = self.client
        if client is None:
            return {}
        web_paths = [r.path.replace("photounlim:/", "/photounlim/", 1)
                     for r in resources if r.path.startswith("photounlim:/")]
        by_web = client.download_links(web_paths)
        return {"photounlim:/" + web.rsplit("/", 1)[-1]: url
                for web, url in by_web.items()}

    def adopt_browser_cookies(self) -> tuple[str, str, bool] | None:
        """Найти куки в локальных браузерах и принять (auto=True).

        Возвращает (браузер, строка Cookie, изменилось ли что-то);
        None — браузеры не отдали Session_id. При изменении испускается
        cookies_changed — лента перезапустится сама.
        """
        from ydx.core import browser_cookies

        login = self._active_login()
        if login is None:
            return None
        found = browser_cookies.find_cookie_header()
        if found is None:
            return None
        browser, header = found
        changed = self._store.load(login) != header
        if changed:
            self._store.save(login, header, auto=True)
            self._reset_client()
            self.cookies_changed.emit()
        return browser, header, changed

    def _refresh_expired(self) -> bool:
        """Куки истекли: если источник — браузер, перечитать оттуда молча."""
        login = self._active_login()
        if login is None or not self._store.is_auto(login):
            return False
        adopted = self.adopt_browser_cookies()
        return bool(adopted and adopted[2])

    def diagnostic_dump(self, raw: str | None = None) -> dict:
        client = UnlimClient(raw) if raw else self.client
        if client is None:
            raise RuntimeError("no cookies")
        return client.diagnostic_dump()

    def save_cookies(self, raw: str) -> None:
        login = self._active_login()
        if login is None:
            raise RuntimeError("no active account")
        parse_cookie_string(raw)          # валидация до сохранения
        self._store.save(login, raw.strip())
        self._reset_client()
        self.cookies_changed.emit()

    def clear_cookies(self) -> None:
        login = self._active_login()
        if login is not None:
            self._store.delete(login)
        self._reset_client()
        self.cookies_changed.emit()

    # --- данные ленты -----------------------------------------------------------

    def iter_media(self, media_type: str | None = None,
                   refresh: bool = False) -> Iterator[Resource]:
        """Лента безлимита: свежий кэш — мгновенно и без сети; устаревший —
        показать сразу и обновить фоном; отсутствует — обойти и сохранить."""
        from ydx.core.errors import UnlimAuthError

        def wanted(r: Resource) -> bool:
            return media_type is None or r.media_type == media_type

        cache = UnlimFeedCache(self._active_login() or "anon")
        cached = cache.load()
        if cached is not None and not refresh and time.time() - cached[0] < FEED_TTL:
            for r in cached[1]:
                if wanted(r):
                    yield r
            return

        client = self.client
        if client is None:
            if cached is not None:            # кук нет — покажем хоть кэш
                for r in cached[1]:
                    if wanted(r):
                        yield r
            return

        seen: set[str] = set()
        fresh: list[Resource] = []
        try:
            if cached is not None:            # устаревший: мгновенно показать…
                for r in cached[1]:
                    if wanted(r):
                        seen.add(r.path)
                        yield r
            for r in client.iter_media():     # …затем обновиться фоном
                fresh.append(r)
                if r.path not in seen and wanted(r):
                    yield r
            cache.save(fresh)
        except UnlimAuthError:
            # куки могли протухнуть: авто-источник обновится молча,
            # cookies_changed перезапустит сбор; иначе ошибка уйдёт наружу
            if self._refresh_expired():
                return
            raise

    def fetch(self, url: str, stream: bool = False):
        client = self.client
        if client is None:
            raise RuntimeError("no cookies")
        return client.fetch(url, stream=stream)


def dump_path() -> Path:
    """Куда «Диагностика» пишет сырой JSON ответов."""
    return const.CACHE_DIR / "unlim_debug.json"
