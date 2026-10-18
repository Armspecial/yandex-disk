"""HTTP-клиент REST API Яндекс.Диска.

Единое место для: авторизации, ретраев, маппинга ошибок.
Потокобезопасен; один экземпляр на аккаунт.

Карантин недокументированного поведения API живёт здесь и в
api_resources/api_transfers — смена поведения Яндекса = правка одного файла.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from ydx.constants import API_BASE
from ydx.core.errors import ApiError, AuthError, NetworkError, specialize

RETRYABLE = {429, 500, 503}
BACKOFFS = (1, 2, 4, 8)
TIMEOUT = 60


class ApiClient:
    """Обёртка над requests для cloud-api.yandex.net.

    token_provider — объект с get_token() -> str и refresh() -> str.
    refresh() вызывается под локом ровно одним потоком при 401,
    остальные ждут и повторяют запрос с новым токеном.
    """

    def __init__(self, token_provider: Any, base_url: str = API_BASE) -> None:
        self._provider = token_provider
        self._base = base_url.rstrip("/")
        self._lock = threading.Lock()
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16)
        self.session.mount("https://", adapter)
        self.session.headers["Accept"] = "application/json"

    # --- удобные обёртки -------------------------------------------------------
    # Первый аргумент — endpoint API («/resources»); query-параметры — kwargs.
    # Имя endpoint (а не path) — из-за частого у Диска параметра ?path=…

    def get(self, endpoint: str, **params: Any) -> Any:
        return self.request("GET", endpoint, params=params)

    def post(self, endpoint: str, **params: Any) -> Any:
        return self.request("POST", endpoint, params=params)

    def put(self, endpoint: str, **params: Any) -> Any:
        return self.request("PUT", endpoint, params=params)

    def delete(self, endpoint: str, **params: Any) -> Any:
        return self.request("DELETE", endpoint, params=params)

    def patch(self, endpoint: str, body: dict[str, Any], **params: Any) -> Any:
        return self.request("PATCH", endpoint, params=params, json=body)

    def request(self, method: str, path: str, **kw: Any) -> Any:
        """Запрос с ретраями; вернуть разобранный JSON (или None)."""
        _, body = self.request_with_status(method, path, **kw)
        return body

    def request_with_status(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout: int = TIMEOUT,
    ) -> tuple[int, Any]:
        url = path if path.startswith("http") else self._base + path
        params = {k: v for k, v in (params or {}).items() if v is not None}

        refreshed = False
        attempt = 0
        while True:
            try:
                resp = self._send(method, url, params, json, timeout)
            except requests.RequestException as exc:
                raise NetworkError(str(exc)) from exc

            if resp.status_code == 401 and not refreshed:
                refreshed = True
                with self._lock:
                    token = self._provider.refresh()
                if token:
                    continue          # сразу повторяем, без backoff-сна
                raise AuthError("token expired and refresh failed")

            if resp.status_code in RETRYABLE and attempt < len(BACKOFFS):
                self._respect_retry_after(resp)
                time.sleep(BACKOFFS[attempt])
                attempt += 1
                continue
            break

        if resp.status_code >= 400:
            raise self._to_error(resp)
        return resp.status_code, self._parse_body(resp)

    # --- произвольные URL (href загрузки/скачивания/превью) ---------------------

    def raw_get(
        self, url: str, with_auth: bool = True, stream: bool = False,
        timeout: int = TIMEOUT,
    ) -> requests.Response:
        """GET по произвольному URL. download-/preview-href требуют
        OAuth-заголовок; upload-href авторизации не принимает."""
        headers = {"Authorization": f"OAuth {self._provider.get_token()}"} if with_auth else {}
        return self.session.get(url, headers=headers, stream=stream, timeout=timeout)

    def raw_put_file(self, url: str, fileobj: Any, timeout: int | None = None) -> requests.Response:
        """PUT байтов файла на upload-href — строго без заголовка авторизации."""
        return self.session.put(url, data=fileobj, timeout=timeout)

    # --- внутреннее ---------------------------------------------------------------

    def _send(
        self, method: str, url: str, params: dict, json: dict | None, timeout: int,
    ) -> requests.Response:
        headers = {"Authorization": f"OAuth {self._provider.get_token()}"}
        return self.session.request(
            method, url, params=params, json=json, headers=headers, timeout=timeout,
        )

    @staticmethod
    def _parse_body(resp: requests.Response) -> Any:
        if resp.status_code == 204 or not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    @staticmethod
    def _respect_retry_after(resp: requests.Response) -> None:
        try:
            delay = int(resp.headers.get("Retry-After", "0"))
        except ValueError:
            delay = 0
        if delay > 0:
            time.sleep(min(delay, 30))

    @staticmethod
    def _to_error(resp: requests.Response) -> ApiError:
        code, description = "", resp.text[:300]
        try:
            body = resp.json()
            code = body.get("error", "")
            description = body.get("description") or code or description
        except ValueError:
            pass
        if resp.status_code == 401:
            return AuthError(f"[401] {code}: {description}")
        return specialize(resp.status_code, code, description)


class StaticTokenProvider:
    """Простейший провайдер: токен известен и не обновляется (ручная вставка)."""

    def __init__(self, token: str) -> None:
        self._token = token

    def get_token(self) -> str:
        return self._token

    def refresh(self) -> str:
        return ""
