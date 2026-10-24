"""UnlimService/UnlimCookieStore: хранение кук по логину, сигналы, чистка."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest


@pytest.fixture()
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def store_dir(tmp_path, monkeypatch):
    from ydx import constants as const

    monkeypatch.setattr(const, "CONFIG_DIR", tmp_path)
    return tmp_path


class _StubSettings:
    def __init__(self, values: dict | None = None) -> None:
        self.values = values or {}

    def get(self, key: str):
        return self.values.get(key, True)


class _Account:
    def __init__(self, login: str) -> None:
        self.login = login


def _accounts(qapp, logins: list[str], active: str | None):
    from PySide6.QtCore import QObject, Signal

    class _Acc(QObject):
        active_changed = Signal(object)
        accounts_changed = Signal(list)

        def __init__(self) -> None:
            super().__init__()
            self.items = [_Account(l) for l in logins]
            self.current = _Account(active) if active else None

        @property
        def active(self):
            return self.current

        @property
        def accounts(self):
            return self.items

    return _Acc()


# --- стор --------------------------------------------------------------------------


def test_store_roundtrip_and_mode(store_dir):
    from ydx.services.unlim import UnlimCookieStore

    store = UnlimCookieStore()
    store.save("user@ya.ru", "Session_id=abc")
    store.save("other@ya.ru", "Session_id=xyz")
    assert store.load("user@ya.ru") == "Session_id=abc"
    assert sorted(store.logins()) == ["other@ya.ru", "user@ya.ru"]

    path = store_dir / "unlim_cookies.json"
    assert path.exists() and (path.stat().st_mode & 0o777) == 0o600

    store.delete("user@ya.ru")
    assert store.load("user@ya.ru") is None
    assert store.logins() == ["other@ya.ru"]


# --- сервис -------------------------------------------------------------------------


def test_save_cookies_emits_and_follows_account(qapp, store_dir):
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["user@ya.ru"], "user@ya.ru")
    service = UnlimService(_StubSettings(), accounts)
    fired = []
    service.cookies_changed.connect(lambda: fired.append(1))

    assert not service.has_cookies
    service.save_cookies("  Session_id=abc; yandex_uid=1  ")
    assert fired == [1]
    assert service.has_cookies
    assert service.client is not None                    # клиент построен

    # куки другого логина не видны под этим аккаунтом
    accounts.current = None
    assert not service.has_cookies


def test_save_invalid_cookies_rejected(qapp, store_dir):
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings(), accounts)
    with pytest.raises(ValueError):
        service.save_cookies("yandex_uid=1")             # без Session_id
    assert not service.has_cookies


def test_clear_cookies(qapp, store_dir):
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings(), accounts)
    service.save_cookies("Session_id=a")
    fired = []
    service.cookies_changed.connect(lambda: fired.append(1))
    service.clear_cookies()
    assert fired == [1]
    assert not service.has_cookies and service.client is None


def test_prune_removed_accounts(qapp, store_dir):
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u", "gone"], "u")
    service = UnlimService(_StubSettings(), accounts)
    service.save_cookies("Session_id=a")
    from ydx.services.unlim import UnlimCookieStore

    UnlimCookieStore().save("gone", "Session_id=old")

    accounts.items = [_Account("u")]                     # «gone» удалён
    accounts.accounts_changed.emit(accounts.items)
    assert UnlimCookieStore().logins() == ["u"]


def test_client_rebuilt_on_active_change(qapp, store_dir):
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings(), accounts)
    service.save_cookies("Session_id=a")
    first = service.client

    accounts.active_changed.emit(None)                   # смена аккаунта
    assert service.client is not first


def test_iter_media_without_cookies(qapp, store_dir):
    """Нет кук — итератор пуст, без сети."""
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings(), accounts)
    from datetime import datetime, timezone  # noqa: F401 — читаемость

    assert list(service.iter_media("image")) == []


def test_enabled_flag(qapp, store_dir):
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings({"show_unlim": False}), accounts)
    assert not service.enabled


# --- авто-поиск кук в браузерах ----------------------------------------------------


def _fake_browser(monkeypatch, result):
    from ydx.core import browser_cookies

    monkeypatch.setattr(browser_cookies, "find_cookie_header", lambda home=None: result)


def test_adopt_browser_cookies_saves_and_signals(qapp, store_dir, monkeypatch):
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings(), accounts)
    fired = []
    service.cookies_changed.connect(lambda: fired.append(1))
    _fake_browser(monkeypatch, ("Yandex Browser", "Session_id=fresh"))

    browser, header, changed = service.adopt_browser_cookies()
    assert (browser, header, changed) == ("Yandex Browser", "Session_id=fresh", True)
    assert fired == [1]
    assert service.has_cookies

    from ydx.services.unlim import UnlimCookieStore

    assert UnlimCookieStore().is_auto("u")           # источник — браузер

    # повторный поиск тех же кук — изменилось ничего, сигнал не дёргается
    again = service.adopt_browser_cookies()
    assert again is not None and again[2] is False
    assert fired == [1]


def test_adopt_without_browser_returns_none(qapp, store_dir, monkeypatch):
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings(), accounts)
    _fake_browser(monkeypatch, None)
    assert service.adopt_browser_cookies() is None
    assert not service.has_cookies


def test_iter_media_refreshes_expired_auto_cookies(qapp, store_dir, monkeypatch):
    """Auth-ошибка у авто-куки → молча перечитать из браузера, без raise."""
    from ydx.core.errors import UnlimAuthError
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings(), accounts)
    _fake_browser(monkeypatch, ("Yandex Browser", "Session_id=old"))
    service.adopt_browser_cookies()                    # источник — браузер (auto)

    class _FailingClient:
        def iter_media(self, media_type=None):
            raise UnlimAuthError("expired")
            yield                                      # pragma: no cover — генератор

    service._client = _FailingClient()
    service._client_login = "u"
    fired = []
    service.cookies_changed.connect(lambda: fired.append(1))
    _fake_browser(monkeypatch, ("Yandex Browser", "Session_id=new"))

    assert list(service.iter_media("image")) == []     # тихо, без исключения
    assert fired == [1]
    assert service.client is not None

    from ydx.services.unlim import UnlimCookieStore

    assert UnlimCookieStore().load("u") == "Session_id=new"


def test_iter_media_keeps_manual_cookies(qapp, store_dir, monkeypatch):
    """Ручные куки при истечении молча не заменяются — ошибка уходит наружу."""
    from ydx.core.errors import UnlimAuthError
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings(), accounts)
    service.save_cookies("Session_id=manual")

    class _FailingClient:
        def iter_media(self, media_type=None):
            raise UnlimAuthError("expired")
            yield                                      # pragma: no cover

    service._client = _FailingClient()
    service._client_login = "u"
    _fake_browser(monkeypatch, ("Yandex Browser", "Session_id=browser"))

    import pytest

    with pytest.raises(UnlimAuthError):
        list(service.iter_media("image"))


# --- кэш ленты ----------------------------------------------------------------------


def _res(path: str, media: str = "image"):
    from datetime import datetime, timezone

    from ydx.core.models import Resource

    return Resource(name=path.rsplit("/", 1)[-1], type="file", path=path,
                    media_type=media,
                    modified=datetime.fromisoformat("2026-10-07T15:53:40+00:00"),
                    preview="https://x/y.jpg")


class _StubClient:
    def __init__(self, items) -> None:
        self.items = items
        self.calls = 0

    def iter_media(self, media_type=None):
        self.calls += 1
        yield from self.items


def test_feed_cache_fresh_serves_without_network(qapp, store_dir, tmp_path, monkeypatch):
    from ydx import constants as const

    monkeypatch.setattr(const, "CACHE_DIR", tmp_path)
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings(), accounts)
    client = _StubClient([_res("photounlim:/a.jpg"), _res("photounlim:/v.mp4", "video")])
    service._client = client
    service._client_login = "u"

    first = list(service.iter_media("image"))          # кэша нет — живой обход
    assert [r.path for r in first] == ["photounlim:/a.jpg"]
    assert client.calls == 1

    client.items = []                                  # «сеть умерла»
    again = list(service.iter_media("image"))          # свежий кэш — без сети
    assert [r.path for r in again] == ["photounlim:/a.jpg"]
    assert client.calls == 1                           # повторного обхода не было

    videos = list(service.iter_media("video"))         # и другой тип тоже из кэша
    assert [r.path for r in videos] == ["photounlim:/v.mp4"]
    assert client.calls == 1


def test_feed_cache_stale_shows_then_refreshes(qapp, store_dir, tmp_path, monkeypatch):
    import json

    from ydx import constants as const

    monkeypatch.setattr(const, "CACHE_DIR", tmp_path)
    from ydx.services.unlim import UnlimFeedCache, UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings(), accounts)
    cache = UnlimFeedCache("u")
    cache.save([_res("photounlim:/old.jpg")])

    path = tmp_path / "unlim_feed_u.json"              # сделать кэш устаревшим
    data = json.loads(path.read_text("utf-8"))
    data["saved_at"] = 0
    path.write_text(json.dumps(data), "utf-8")

    client = _StubClient([_res("photounlim:/old.jpg"), _res("photounlim:/new.jpg")])
    service._client = client
    service._client_login = "u"

    out = list(service.iter_media())
    assert [r.path for r in out] == ["photounlim:/old.jpg", "photounlim:/new.jpg"]
    assert client.calls == 1

    saved_at, items = cache.load()                     # кэш обновился
    assert saved_at > 0 and len(items) == 2


def test_feed_cache_roundtrip_dates(qapp, store_dir, tmp_path, monkeypatch):
    from ydx import constants as const

    monkeypatch.setattr(const, "CACHE_DIR", tmp_path)
    from ydx.services.unlim import UnlimFeedCache

    cache = UnlimFeedCache("u")
    item = _res("photounlim:/a.jpg")
    cache.save([item])
    _saved_at, loaded = cache.load()
    assert loaded[0].path == item.path
    assert loaded[0].modified == item.modified         # даты пережили JSON
    cache.clear()
    assert cache.load() is None


# --- EXIF-даты REST-фото -------------------------------------------------------


def test_refresh_date_overrides(qapp, store_dir, tmp_path, monkeypatch):
    """Разошедшийся день → переопределение; совпавший и пустой — нет."""
    from datetime import datetime, timezone
    from ydx import constants as const

    monkeypatch.setattr(const, "CACHE_DIR", tmp_path)
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings(), accounts)
    service._sleep = lambda _s: None

    oct7 = datetime(2026, 10, 7, 15, 53, 40, tzinfo=timezone.utc)
    oct10 = datetime(2026, 10, 10, 9, 0, 0, tzinfo=timezone.utc)

    class _FakeClient:
        def bulk_info(self, paths):
            return {
                "/disk/Cam/rescued.jpg": {"meta": {"etime": int(oct7.timestamp())}},
                "/disk/Cam/fresh.jpg": {"meta": {"etime": int(oct10.timestamp())}},
                "/disk/Cam/noexif.jpg": {},
            }

    service._client = _FakeClient()
    service._client_login = "u"
    fired = []
    service.dates_changed.connect(lambda: fired.append(1))

    changed = service.refresh_date_overrides([
        ("disk:/Cam/rescued.jpg", oct10),              # съёмка 7-е, изменение 10-е
        ("disk:/Cam/fresh.jpg", oct10),                # совпало — не трогаем
        ("disk:/Cam/noexif.jpg", oct10),               # без EXIF — мимо
        ("photounlim:/x.jpg", oct7),                   # не disk:/ — мимо
    ])
    assert changed == 1
    assert fired == [1]
    overrides = service.date_overrides()
    assert overrides == {"disk:/Cam/rescued.jpg": oct7}
    assert service.redates_fresh()                     # кэш дат свежий

    # повторный прогон: те же расхождения — ничего нового, сигнал молчит
    changed2 = service.refresh_date_overrides([
        ("disk:/Cam/rescued.jpg", oct10), ("disk:/Cam/fresh.jpg", oct10)])
    assert changed2 == 0
    assert fired == [1]


def test_preview_url_refresh(qapp, store_dir, monkeypatch):
    """Свежая ссылка превью для фотounlim-пути; без кук и не-photounlim — None."""
    from ydx.services.unlim import UnlimService

    accounts = _accounts(qapp, ["u"], "u")
    service = UnlimService(_StubSettings(), accounts)
    assert service.preview_url("photounlim:/x.jpg") is None      # кук нет

    service.save_cookies("Session_id=t")
    assert service.preview_url("disk:/x.jpg") is None            # не безлимит

    class _FakeClient:
        def bulk_info(self, paths):
            return {"/photounlim/pic.jpg": {
                "id": "/photounlim/pic.jpg", "name": "pic.jpg",
                "meta": {"mimetype": "image/jpeg", "etime": 1791388420,
                         "sizes": [{"name": "XL", "url": "//2.t/preview/x"}]},
            }}

    service._client = _FakeClient()
    service._client_login = "u"
    assert service.preview_url("photounlim:/pic.jpg") == "https://2.t/preview/x"
