"""Менеджер аккаунтов: до MAX_ACCOUNTS записей, активный аккаунт, ApiClient.

Токены живут в TokenStore; провайдер токена обновляет их под локом при 401
(ротация refresh-токена: новый сохраняется до сброса старого).
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from PySide6.QtCore import QObject, Signal

from ydx.constants import MAX_ACCOUNTS
from ydx.core.api_client import ApiClient
from ydx.core.api_resources import ResourcesApi
from ydx.core.errors import AuthError
from ydx.core.models import Account, Quota, TokenBundle
from ydx.core.oauth import OAuthClient
from ydx.services.settings import AppSettings
from ydx.services.token_store import TokenStore
from ydx.services.workers import run_in_thread

AVATAR_COLORS = ["#FFCC00", "#FC3F1D", "#1DA740", "#2E6BE6", "#9B4DE0", "#00B8B0"]


class _TokenProvider:
    """Отдаёт access_token; умеет refresh под локом (для ApiClient)."""

    def __init__(self, store: TokenStore, account_key: str, settings: AppSettings) -> None:
        self._store = store
        self._key = account_key
        self._settings = settings
        self._lock = threading.Lock()
        self._tokens: dict[str, Any] = store.load(account_key) or {}

    def get_token(self) -> str:
        return self._tokens.get("access_token", "")

    def refresh(self) -> str:
        with self._lock:
            current = self._store.load(self._key) or {}
            refresh_token = current.get("refresh_token")
            if not refresh_token:
                return ""
            oauth = OAuthClient(self._settings.client_id, self._settings.client_secret)
            bundle = oauth.refresh(refresh_token)
            new_tokens = {
                "access_token": bundle.access_token,
                "refresh_token": bundle.refresh_token or refresh_token,
                "expires_at": bundle.expires_at,
            }
            # сначала сохраняем новый, только потом forget старый (ротация!)
            self._store.save(self._key, new_tokens)
            self._tokens = new_tokens
            return new_tokens["access_token"]


class AccountManager(QObject):
    accounts_changed = Signal(list)          # list[Account]
    active_changed = Signal(object)          # Account | None
    quota_updated = Signal(object)           # Quota
    auth_failed = Signal(object)             # Account — нужен повторный вход

    def __init__(self, settings: AppSettings, store: TokenStore) -> None:
        super().__init__()
        self._settings = settings
        self._store = store
        self.accounts: list[Account] = []
        self._api: ApiClient | None = None
        self._provider: _TokenProvider | None = None
        self._active_key = ""
        self._load()

    # --- персистентность -------------------------------------------------------

    def _load(self) -> None:
        try:
            raw = json.loads(self._settings.get("accounts") or "[]")
        except ValueError:
            raw = []
        self.accounts = [
            Account(
                login=a.get("login", ""),
                display_name=a.get("display_name", ""),
                color_index=a.get("color_index", i),
            )
            for i, a in enumerate(raw)
        ]
        self._active_key = self._settings.get("active_account") or ""

    def _persist(self) -> None:
        self._settings.set(
            "accounts",
            json.dumps(
                [
                    {"login": a.login, "display_name": a.display_name, "color_index": a.color_index}
                    for a in self.accounts
                ],
                ensure_ascii=False,
            ),
        )
        self._settings.set("active_account", self._active_key)

    # --- доступ -------------------------------------------------------------------

    @property
    def active(self) -> Account | None:
        for a in self.accounts:
            if a.login == self._active_key:
                return a
        return None

    @property
    def api(self) -> ApiClient | None:
        return self._api

    @property
    def resources(self) -> ResourcesApi | None:
        return ResourcesApi(self._api) if self._api else None

    def current_token(self) -> str:
        return self._provider.get_token() if self._provider is not None else ""

    def _activate(self, account: Account | None) -> None:
        if account is None:
            self._api = None
            self._provider = None
            self._active_key = ""
        else:
            self._active_key = account.login
            self._provider = _TokenProvider(self._store, account.login, self._settings)
            self._api = ApiClient(self._provider)
        self._persist()
        self.active_changed.emit(account)

    # --- действия -------------------------------------------------------------------

    def add_account(self, bundle: TokenBundle, display_name: str) -> Account:
        login = bundle.login or f"account-{uuid.uuid4().hex[:8]}"
        # если такой логин уже добавлен — перезаписываем токены (перезалогин)
        existing = next((a for a in self.accounts if a.login == login), None)
        if existing is not None:
            account = existing
        elif len(self.accounts) >= MAX_ACCOUNTS:
            raise AuthError("Достигнут лимит аккаунтов (2)")
        else:
            account = Account(
                login=login,
                display_name=display_name,
                color_index=len(self.accounts) % len(AVATAR_COLORS),
            )
            self.accounts.append(account)
        self._store.save(login, {
            "access_token": bundle.access_token,
            "refresh_token": bundle.refresh_token,
            "expires_at": bundle.expires_at,
        })
        self._persist()
        self.accounts_changed.emit(list(self.accounts))
        self._activate(account)
        return account

    def switch_to(self, login: str) -> None:
        account = next((a for a in self.accounts if a.login == login), None)
        if account and account.login != self._active_key:
            self._activate(account)

    def logout(self, login: str | None = None) -> None:
        login = login or self._active_key
        self.accounts = [a for a in self.accounts if a.login != login]
        self._store.delete(login)
        if login == self._active_key:
            self._activate(self.accounts[0] if self.accounts else None)
        else:
            self._persist()
        self.accounts_changed.emit(list(self.accounts))

    # --- квота --------------------------------------------------------------------------

    def refresh_quota(self) -> None:
        resources = self.resources
        if resources is None:
            return

        def job() -> Quota:
            quota, _ = resources.disk_info()
            return quota

        run_in_thread(
            job,
            on_done=self.quota_updated.emit,
            on_error=self._on_quota_error,
        )

    def _on_quota_error(self, exc: Exception) -> None:
        if isinstance(exc, AuthError):
            self.auth_failed.emit(self.active)
        # сетевые ошибки квоты молча пропускаем — покажет следующий успешный запрос

    def start(self) -> None:
        """Активировать сохранённый или первый аккаунт после запуска."""
        if self.accounts and not self.active:
            self._activate(self.accounts[0])
        elif self.active:
            self._activate(self.active)   # построить ApiClient
