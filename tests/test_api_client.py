"""Тесты ApiClient: маппинг ошибок, ретраи, кодировка путей."""

from __future__ import annotations

import json

import pytest
import responses as rsplib

from ydx.core.api_client import ApiClient, StaticTokenProvider
from ydx.core.errors import ApiError, AuthError, ConflictError, DiskFullError, NetworkError, NotFoundError

BASE = "https://cloud-api.yandex.net/v1/disk"


@pytest.fixture()
def client():
    return ApiClient(StaticTokenProvider("tok"))


@rsplib.activate
def test_auth_header_and_params_encoding(client):
    rsplib.add(rsplib.GET, f"{BASE}/resources", json={"name": "тест 🎉"})
    out = client.get("/resources", path="disk:/папка/файл 🎉.txt")
    assert out == {"name": "тест 🎉"}
    req = rsplib.calls[0].request
    assert req.headers["Authorization"] == "OAuth tok"
    assert "%D0%BF%D0%B0%D0%BF%D0%BA%D0%B0" in req.url  # «папка» закодирована один раз


@rsplib.activate
def test_error_specialization(client):
    rsplib.add(rsplib.GET, f"{BASE}/resources", status=404,
               json={"error": "DiskNotFoundError", "description": "not found"})
    with pytest.raises(NotFoundError):
        client.get("/resources", path="disk:/nope")

    rsplib.reset()
    rsplib.add(rsplib.GET, f"{BASE}/resources", status=409,
               json={"error": "DiskPathAlreadyExistsError", "description": "exists"})
    with pytest.raises(ConflictError):
        client.get("/resources", path="disk:/x")

    rsplib.reset()
    rsplib.add(rsplib.DELETE, f"{BASE}/resources", status=507,
               json={"error": "DiskResourceDoesNotExistError", "description": "no"})
    with pytest.raises(DiskFullError):
        client.delete("/resources", path="disk:/x")


@rsplib.activate
def test_backoff_on_429(client, monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr("ydx.core.api_client.time.sleep", lambda s: sleeps.append(s))
    rsplib.add(rsplib.GET, f"{BASE}/", status=429, json={"error": "too many"})
    rsplib.add(rsplib.GET, f"{BASE}/", status=429, json={"error": "too many"})
    rsplib.add(rsplib.GET, f"{BASE}/", status=200, json={"used_space": 1})
    out = client.get("/")
    assert out == {"used_space": 1}
    assert sleeps == [1, 2]  # между тремя попытками два сна


@rsplib.activate
def test_401_refresh_retry(monkeypatch):
    class Provider:
        def __init__(self):
            self.refreshed = 0
            self.token = "tok"

        def get_token(self):
            return self.token

        def refresh(self):
            self.refreshed += 1
            self.token = "tok2"
            return self.token

    provider = Provider()
    client = ApiClient(provider)
    rsplib.add(rsplib.GET, f"{BASE}/", status=401, json={"error": "unauthorized"})
    rsplib.add(rsplib.GET, f"{BASE}/", status=200, json={"ok": 1})
    out = client.get("/")
    assert out == {"ok": 1}
    assert provider.refreshed == 1
    assert rsplib.calls[1].request.headers["Authorization"] == "OAuth tok2"


@rsplib.activate
def test_401_no_refresh_raises():
    class DeadProvider(StaticTokenProvider):
        def refresh(self):
            return ""

    client = ApiClient(DeadProvider("tok"))
    rsplib.add(rsplib.GET, f"{BASE}/", status=401, json={"error": "unauthorized"})
    with pytest.raises(AuthError):
        client.get("/")


@rsplib.activate
def test_network_error_wrapped(client):
    import requests as requests_lib

    rsplib.add(rsplib.GET, f"{BASE}/", body=requests_lib.exceptions.ConnectionError("boom"))
    with pytest.raises(NetworkError):
        client.get("/")


def test_specialize_fallback():
    from ydx.core.errors import specialize
    err = specialize(400, "SomethingElse", "d")
    assert type(err) is ApiError
