"""Дисковый кэш REST-части ленты «Фото»: мгновенный показ после запуска.

Обход дерева Диска долог (минуты и десятки запросов на каждый запуск),
поэтому вместе с ревизией Диска снимок ленты лежит в ~/.cache: раздел
открывается из кэша сразу, затем одной проверкой revision выясняется,
менялся ли Диск.
Не менялся — обхода нет вовсе; менялся — фоновый обход обновляет кэш,
а новые фото догружаются в ленту по ходу.
"""

from __future__ import annotations

import json
import logging
import re
import time

from ydx import constants as const
from ydx.core.models import Resource
from ydx.services.unlim import _resource_from_dict, _resource_to_dict

log = logging.getLogger(__name__)


class RestFeedCache:
    """Лента REST-фото одного логина в ~/.cache: {saved_at, revision, items[]}."""

    def __init__(self, login: str) -> None:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", login)[:60] or "anon"
        self._file = const.CACHE_DIR / f"rest_feed_{safe}.json"

    def load(self) -> tuple[int, list[Resource]] | None:
        """(ревизия Диска на момент обхода, элементы) или None."""
        try:
            data = json.loads(self._file.read_text("utf-8"))
            revision = int(data.get("revision") or 0)
            items = [_resource_from_dict(d) for d in data.get("items") or []]
        except (OSError, ValueError, TypeError):
            return None
        if not items:
            return None
        return revision, items

    def save(self, revision: int, items: list[Resource]) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "saved_at": time.time(),
                "revision": revision,
                "items": [_resource_to_dict(r) for r in items],
            }, ensure_ascii=False), "utf-8")
            tmp.replace(self._file)
        except OSError as exc:
            log.debug("rest feed cache write failed: %s", exc)
