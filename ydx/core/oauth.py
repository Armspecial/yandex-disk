"""OAuth 2.0 Яндекс ID: три способа входа + refresh.

Потоки:
- authorization_code — браузер -> redirect на http://localhost:8917 (RedirectListener);
- device_code       — код пользователя на oauth.yandex.ru/device (primary, без redirect URI);
- вставка токена    — обходной путь, без refresh.

Refresh-токены Яндекса ротируются: новый refresh сохранять ДО сброса старого.
"""

from __future__ import annotations

import queue
import secrets
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from ydx.constants import OAUTH_BASE, REDIRECT_PORT, REDIRECT_URI, SCOPES
from ydx.core.errors import AuthError
from ydx.core.models import TokenBundle

LOGIN_INFO_URL = "https://login.yandex.ru/info"
DEVICE_URL = OAUTH_BASE + "/device/code"
TOKEN_URL = OAUTH_BASE + "/token"
AUTHORIZE_URL = OAUTH_BASE + "/authorize"

USER_AGENT = "yandex-disk-linux"


class OAuthClient:
    def __init__(self, client_id: str, client_secret: str) -> None:
        if not client_id:
            raise AuthError("client_id не задан — зарегистрируйте приложение на oauth.yandex.ru")
        self.client_id = client_id
        self.client_secret = client_secret

    # --- authorization code flow ----------------------------------------------

    def authorize_url(self, state: str) -> str:
        qs = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "state": state,
            "force_confirm": "yes",
        }
        return requests.Request("GET", AUTHORIZE_URL, params=qs).prepare().url

    def exchange_code(self, code: str) -> TokenBundle:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        return self._token_request(data)

    # --- device code flow ---------------------------------------------------------

    def start_device_code(self, device_id: str, device_name: str) -> "DeviceCodeInfo":
        resp = requests.post(
            DEVICE_URL,
            data={
                "client_id": self.client_id,
                "device_id": device_id,
                "device_name": device_name,
            },
            timeout=20,
        )
        if resp.status_code >= 400:
            raise AuthError(f"device code failed: {resp.status_code} {resp.text[:200]}")
        d = resp.json()
        return DeviceCodeInfo(
            device_code=d["device_code"],
            user_code=d["user_code"],
            verification_url=d.get("verification_url", OAUTH_BASE + "/device"),
            interval=float(d.get("interval", 5)),
            expires_in=float(d.get("expires_in", 300)),
        )

    def poll_device_code(
        self, info: "DeviceCodeInfo", should_cancel=None,
    ) -> "TokenBundle":
        """Блокирующий опрос (вызывать в воркере)."""
        deadline = time.monotonic() + info.expires_in
        interval = info.interval
        while time.monotonic() < deadline:
            if should_cancel and should_cancel():
                raise AuthError("cancelled")
            time.sleep(interval)
            data = {
                "grant_type": "device_code",
                "code": info.device_code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
            resp = self._post_token(data)
            if resp.status_code == 200:
                return self._bundle(resp.json())
            err = resp.json().get("error", "")
            if err == "authorization_pending":
                continue
            if err == "slow_down":
                interval += 5
                continue
            raise AuthError(f"device flow error: {err}")
        raise AuthError("device code expired")

    # --- refresh --------------------------------------------------------------------

    def refresh(self, refresh_token: str) -> "TokenBundle":
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        return self._token_request(data)

    # --- служебное ----------------------------------------------------------------

    def _post_token(self, data: dict[str, str]) -> requests.Response:
        return requests.post(
            TOKEN_URL,
            data=data,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )

    def _token_request(self, data: dict[str, str]) -> "TokenBundle":
        resp = self._post_token(data)
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", str(resp.status_code))
            except ValueError:
                err = str(resp.status_code)
            raise AuthError(f"token request failed: {err}")
        return self._bundle(resp.json())

    @staticmethod
    def _bundle(d: dict) -> "TokenBundle":
        return TokenBundle(
            access_token=d["access_token"],
            refresh_token=d.get("refresh_token"),
            expires_at=(time.time() + d["expires_in"]) if d.get("expires_in") else None,
        )

    @staticmethod
    def fetch_login(access_token: str) -> tuple[str | None, str]:
        """Логин/имя через Яндекс ID (если прав не выдано — (None, ''))."""
        try:
            r = requests.get(
                LOGIN_INFO_URL,
                headers={"Authorization": f"OAuth {access_token}"},
                timeout=15,
            )
            if r.status_code == 200:
                d = r.json()
                return d.get("login"), (d.get("display_name") or d.get("real_name") or "")
        except requests.RequestException:
            pass
        return None, ""


@dataclass(frozen=True)
class DeviceCodeInfo:
    device_code: str
    user_code: str
    verification_url: str
    interval: float
    expires_in: float


class RedirectListener:
    """Одноразовый HTTP-сервер на localhost:8917 для перехвата ?code=…"""

    def __init__(self, port: int = REDIRECT_PORT) -> None:
        self.port = port
        self._q: queue.Queue[tuple[str, str]] = queue.Queue()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — имя метода http.server
                from urllib.parse import parse_qs, urlparse
                parsed = urlparse(self.path)
                qs = parse_qs(parsed.query)
                code = qs.get("code", [""])[0]
                state = qs.get("state", [""])[0]
                if code:
                    outer._q.put((code, state))
                    body = (
                        "<html><body style='font-family:sans-serif;text-align:center;"
                        "padding-top:60px'><h2>Вход выполнен</h2>"
                        "<p>Можно вернуться в приложение «Яндекс Диск».</p></body></html>"
                    ).encode("utf-8")
                else:
                    body = (
                        "<html><body><h2>Ожидание авторизации…</h2></body></html>"
                    ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:  # тишина в stderr
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="oauth-redirect"
        )
        self._thread.start()

    def wait_code(self, timeout: float, expected_state: str) -> str:
        """Ждать код; проверяет state. Возвращает code или бросает AuthError."""
        try:
            code, state = self._q.get(timeout=timeout)
        except queue.Empty as exc:
            raise AuthError("redirect wait timeout") from exc
        if state != expected_state:
            raise AuthError("state mismatch")
        return code

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None


def new_state() -> str:
    return secrets.token_hex(16)
