"""Ротация refresh-токена: fallback на историю прошлых токенов.

Краш между выдачей Яндексом нового refresh и его сохранением оставляет
на диске старый (возможно, ещё валидный) токен — refresh обязан пробовать
его, прежде чем признавать разлогин.
"""

import pytest

from ydx.core.errors import AuthError
from ydx.core.models import TokenBundle
from ydx.services import accounts as accounts_mod
from ydx.services.accounts import _TokenProvider


class _Store:
    def __init__(self, data):
        self.data = data

    def load(self, key):
        return self.data.get(key)

    def save(self, key, tokens):
        self.data[key] = tokens


class _Settings:
    client_id = "id"
    client_secret = "secret"


def _oauth_with_valid(valid):
    class _OAuth:
        def __init__(self, client_id, client_secret):
            pass

        def refresh(self, token):
            if token not in valid:
                raise AuthError(f"invalid token: {token}")
            return TokenBundle(
                access_token="acc-new", refresh_token="rot-next", expires_at=1.0
            )

    return _OAuth


def test_refresh_falls_back_to_history(monkeypatch):
    monkeypatch.setattr(accounts_mod, "OAuthClient", _oauth_with_valid({"old-ok"}))
    store = _Store(
        {"u": {"access_token": "a", "refresh_token": "dead", "refresh_history": ["old-ok"]}}
    )
    provider = _TokenProvider(store, "u", _Settings())

    assert provider.refresh() == "acc-new"
    saved = store.data["u"]
    assert saved["refresh_token"] == "rot-next"
    # отклонённый «dead» в историю не возвращается
    assert saved["refresh_history"] == ["old-ok"]


def test_refresh_all_dead_raises(monkeypatch):
    monkeypatch.setattr(accounts_mod, "OAuthClient", _oauth_with_valid(set()))
    store = _Store({"u": {"access_token": "a", "refresh_token": "dead"}})
    provider = _TokenProvider(store, "u", _Settings())

    with pytest.raises(AuthError):
        provider.refresh()


def test_refresh_no_tokens_returns_empty(monkeypatch):
    monkeypatch.setattr(accounts_mod, "OAuthClient", _oauth_with_valid({"x"}))
    provider = _TokenProvider(_Store({}), "u", _Settings())

    assert provider.refresh() == ""


def test_refresh_keeps_previous_in_history(monkeypatch):
    monkeypatch.setattr(accounts_mod, "OAuthClient", _oauth_with_valid({"cur"}))
    store = _Store({"u": {"access_token": "a", "refresh_token": "cur"}})
    provider = _TokenProvider(store, "u", _Settings())

    assert provider.refresh() == "acc-new"
    assert store.data["u"]["refresh_history"] == ["cur"]
