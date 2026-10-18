"""Типизированные методы REST API Яндекс.Диска (ресурсы, корзина, публикация).

Карантин особенностей API:
- _embedded.items всегда сортирует папки вперёд файлов, независимо от sort;
- пагинация offset/limit до _embedded.total;
- мусорные пути имеют суффиксы вида trash:/foo_123456;
- 202 + Link на операцию для «тяжёлых» copy/move/delete.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ydx.constants import MAX_PAGES, PAGE_SIZE
from ydx.core.api_client import ApiClient
from ydx.core.models import Operation, Quota, Resource


class ResourcesApi:
    def __init__(self, client: ApiClient) -> None:
        self.api = client

    # --- диск и метаданные ---------------------------------------------------

    def disk_info(self) -> tuple[Quota, dict[str, str]]:
        d = self.api.get("/")
        quota = Quota(
            total=d.get("total_space") or 0,
            used=d.get("used_space") or 0,
            trash_size=d.get("trash_size") or 0,
        )
        return quota, d.get("system_folders") or {}

    def get(self, path: str, **params: Any) -> Resource:
        d = self.api.get("/resources", path=path, **params)
        return Resource.from_api(d)

    def list_page(
        self, path: str, offset: int = 0, limit: int = PAGE_SIZE, sort: str | None = None,
        preview_size: str = "M", preview_crop: bool = True,
    ) -> tuple[list[Resource], int | None]:
        """Одна страница листинга папки → (элементы, всего-в-папке)."""
        d = self.api.get(
            "/resources",
            path=path,
            offset=offset,
            limit=limit,
            sort=sort,
            preview_size=preview_size,
            preview_crop="true" if preview_crop else None,
        )
        embedded = d.get("_embedded") or {}
        items = [Resource.from_api(i) for i in embedded.get("items") or []]
        return items, embedded.get("total")

    def iter_dir(
        self, path: str, sort: str | None = None, page: int = PAGE_SIZE,
    ) -> Iterator[Resource]:
        """Вся папка с пагинацией (генератор)."""
        offset = 0
        total: int | None = None
        for _ in range(MAX_PAGES):
            items, total = self.list_page(path, offset=offset, limit=page, sort=sort)
            yield from items
            if total is None:
                break
            offset += page
            if offset >= total:
                break

    def flat_files(
        self, media_type: str | None = None, offset: int = 0, limit: int = PAGE_SIZE,
        preview_size: str = "M", preview_crop: bool = True,
    ) -> tuple[list[Resource], int | None]:
        """Плоский список всех файлов (с фильтром media_type).

        FilesResourceList не возвращает total — возвращаем has_more.
        """
        d = self.api.get(
            "/resources/files",
            offset=offset,
            limit=limit,
            media_type=media_type,
            preview_size=preview_size,
            preview_crop="true" if preview_crop else None,
        )
        items = [Resource.from_api(i) for i in d.get("items") or []]
        has_more = len(items) >= limit
        return items, (offset + len(items) if has_more else None)

    def last_uploaded(
        self, limit: int = 50, media_type: str | None = None,
        preview_size: str = "M", preview_crop: bool = True,
    ) -> list[Resource]:
        d = self.api.get(
            "/resources/last-uploaded",
            limit=limit,
            media_type=media_type,
            preview_size=preview_size,
            preview_crop="true" if preview_crop else None,
        )
        return [Resource.from_api(i) for i in d.get("items") or []]

    # --- изменение структуры ---------------------------------------------------

    def mkdir(self, path: str) -> None:
        self.api.put("/resources", path=path)

    def copy(self, src: str, dst: str, overwrite: bool = False) -> Operation | None:
        return self._maybe_async("copy", src=src, path=dst, overwrite="true" if overwrite else None)

    def move(self, src: str, dst: str, overwrite: bool = False) -> Operation | None:
        """Также используется как rename (move внутри той же папки)."""
        return self._maybe_async("move", src=src, path=dst, overwrite="true" if overwrite else None)

    def delete(self, path: str, permanently: bool = False) -> Operation | None:
        return self._maybe_async(
            "delete", path=path, permanently="true" if permanently else None
        )

    def _maybe_async(self, action: str, **params: Any) -> Operation | None:
        """POST/DELETE: 201/204 — готово; 202 — вернуть Operation для поллинга.

        Важно: 201 тоже возвращает Link, но он указывает на метаданные
        ресурса, а не на статус операции — операция только при 202.
        DELETE /resources (без суффикса!) — удаление, copy/move — POST с суффиксом.
        """
        method, endpoint = ("DELETE", "/resources") if action == "delete" else (
            "POST", f"/resources/{action}"
        )
        status, body = self.api.request_with_status(method, endpoint, params=params)
        if status == 202 and isinstance(body, dict) and body.get("href"):
            return Operation(href=body["href"])
        return None

    def patch_properties(self, path: str, props: dict[str, str]) -> Resource:
        d = self.api.patch("/resources", {"custom_properties": props}, path=path)
        return Resource.from_api(d)

    # --- публичные ссылки --------------------------------------------------------

    def publish(self, path: str) -> Resource:
        """Опубликовать; возвращает ресурс с public_url."""
        d = self.api.put("/resources/publish", path=path)
        # ответ — Link; перечитаем метаданные, чтобы забрать public_url
        return self.get(path, fields="name,path,type,public_url,public_key,preview")

    def unpublish(self, path: str) -> Resource:
        self.api.put("/resources/unpublish", path=path)
        return self.get(path, fields="name,path,type,public_url,public_key")

    def list_public(
        self, offset: int = 0, limit: int = PAGE_SIZE, preview_size: str = "M",
    ) -> tuple[list[Resource], int | None]:
        """Список опубликованных мной ресурсов (новые сверху)."""
        d = self.api.get(
            "/resources/public", offset=offset, limit=limit, preview_size=preview_size
        )
        items = [Resource.from_api(i) for i in d.get("items") or []]
        return items, d.get("total") if "total" in d else (offset + len(items) if len(items) >= limit else None)

    # --- корзина -----------------------------------------------------------------

    def trash_list(
        self, path: str = "/", offset: int = 0, limit: int = PAGE_SIZE,
    ) -> tuple[list[Resource], int | None]:
        d = self.api.get("/trash/resources", path=path, offset=offset, limit=limit)
        embedded = d.get("_embedded") or {}
        items = [Resource.from_api(i) for i in embedded.get("items") or []]
        return items, embedded.get("total")

    def trash_restore(
        self, path: str, name: str | None = None, overwrite: bool = False,
    ) -> Operation | None:
        status, body = self.api.request_with_status(
            "PUT",
            "/trash/resources/restore",
            params={
                "path": path,
                "name": name,
                "overwrite": "true" if overwrite else None,
            },
        )
        if status == 202 and isinstance(body, dict) and body.get("href"):
            return Operation(href=body["href"])
        return None

    def trash_delete(self, path: str | None = None) -> Operation | None:
        """Удалить из корзины навсегда; path=None — очистить корзину целиком."""
        status, body = self.api.request_with_status(
            "DELETE", "/trash/resources", params={"path": path}
        )
        if status == 202 and isinstance(body, dict) and body.get("href"):
            return Operation(href=body["href"])
        return None

    # --- превью/загрузка href ---------------------------------------------------

    def download_href(self, path: str) -> str:
        d = self.api.get("/resources/download", path=path)
        return d["href"]

    def upload_href(self, path: str, overwrite: bool = False) -> str:
        """href живёт 30 минут; PUT байтов — строго без авторизации."""
        d = self.api.get(
            "/resources/upload", path=path, overwrite="true" if overwrite else None
        )
        return d["href"]
