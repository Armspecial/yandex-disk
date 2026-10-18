"""OAuth: построение URL авторизации, парсинг ответов токена."""

from __future__ import annotations

import pytest
import responses as rsplib

from ydx.core.errors import AuthError
from ydx.core.oauth import OAuthClient
from ydx.constants import REDIRECT_URI


def test_authorize_url():
    oauth = OAuthClient("cid", "csecret")
    url = oauth.authorize_url("st4te")
    assert url.startswith("https://oauth.yandex.ru/authorize?")
    assert "response_type=code" in url
    assert "client_id=cid" in url
    assert f"redirect_uri={REDIRECT_URI.replace(':', '%3A').replace('/', '%2F')}" in url
    assert "scope=cloud_api%3Adisk.read" in url
    assert "state=st4te" in url


def test_requires_client_id():
    with pytest.raises(AuthError):
        OAuthClient("", "")


@rsplib.activate
def test_exchange_code_parses_bundle():
    oauth = OAuthClient("cid", "csecret")
    rsplib.add(rsplib.POST, "https://oauth.yandex.ru/token",
               json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600})
    bundle = oauth.exchange_code("c0de")
    assert bundle.access_token == "at"
    assert bundle.refresh_token == "rt"
    assert bundle.expires_at is not None and bundle.expires_at > 0


@rsplib.activate
def test_refresh_error_raises_auth():
    oauth = OAuthClient("cid", "csecret")
    rsplib.add(rsplib.POST, "https://oauth.yandex.ru/token",
               status=400, json={"error": "invalid_grant"})
    with pytest.raises(AuthError):
        oauth.refresh("dead")
