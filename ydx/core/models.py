"""Модели данных (frozen dataclasses). За границами core словари API не проходят."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class Account:
    """Учётная запись. Токены хранятся отдельно (TokenStore), не здесь."""

    login: str                      # идентификатор аккаунта (уникальный)
    display_name: str = ""
    color_index: int = 0            # индекс цвета аватара из инициалов
    avatar_url: str = ""            # аватар Яндекс ID (если выдано право login:info)

    @property
    def title(self) -> str:
        return self.display_name or self.login or "Аккаунт"


@dataclass(frozen=True)
class Quota:
    total: int = 0
    used: int = 0
    trash_size: int = 0

    @property
    def free(self) -> int:
        return max(self.total - self.used, 0)


@dataclass(frozen=True)
class Resource:
    name: str
    type: str                       # 'dir' | 'file'
    path: str                       # 'disk:/...' или 'trash:/...'
    size: int = 0
    created: datetime | None = None
    modified: datetime | None = None
    md5: str | None = None
    mime_type: str | None = None
    media_type: str | None = None   # image/video/document/...
    preview: str | None = None      # URL превью (нужен OAuth-заголовок)
    public_url: str | None = None   # yadi.sk-ссылка, если опубликован
    public_key: str | None = None
    origin_path: str | None = None  # исходный путь (только для корзины)
    total: int | None = None        # всего элементов в папке (для листинга)
    file_url: str | None = None     # прямая ссылка на файл (photounlim)

    @property
    def is_dir(self) -> bool:
        return self.type == "dir"

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Resource":
        embedded = d.get("_embedded") or {}
        return cls(
            name=d.get("name") or "",
            type=d.get("type") or "file",
            path=d.get("path") or "",
            size=d.get("size") or 0,
            created=_parse_dt(d.get("created")),
            modified=_parse_dt(d.get("modified")),
            md5=d.get("md5"),
            mime_type=d.get("mime_type"),
            media_type=d.get("media_type"),
            preview=d.get("preview"),
            public_url=d.get("public_url"),
            public_key=d.get("public_key"),
            origin_path=d.get("origin_path"),
            total=embedded.get("total"),
        )


@dataclass(frozen=True)
class Operation:
    """Асинхронная операция API (ответ 202)."""

    href: str                       # URL статуса
    status: str = "in-progress"     # in-progress | success | failed

    @property
    def done(self) -> bool:
        return self.status in ("success", "failed")


@dataclass(frozen=True)
class TokenBundle:
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None   # unix-time; None = «время жизни не известно»
    login: str | None = None          # если удалось узнать через Яндекс ID


@dataclass
class DaemonStatus:
    state: str = "unknown"          # unknown|stopped|idle|busy|paused|index|no-internet|error
    progress: str = ""              # «45% (10 MB/s)» — сырая строка демона
    last_items: list[str] = field(default_factory=list)
    total: int = 0                  # байты, занятые на Диске (по данным демона)
    used: int = 0
    free: int = 0
    trash: int = 0
    raw: str = ""


@dataclass
class TransferTask:
    """Задача передачи (upload/download)."""

    id: int
    kind: str                       # 'upload' | 'download'
    local_path: str = ""
    remote_path: str = ""           # 'disk:/...'
    size: int = 0
    transferred: int = 0
    state: str = "queued"           # queued|running|paused|done|error|conflict|cancelled
    error: str | None = None
    overwrite: bool = False
    resource: Resource | None = None  # источник (для download)
