"""Превью изображений: память → диск → API (4 параллельных загрузчика).

Ключ — md5 ресурса (fallback sha1(path)); на диск пишется XL-превью,
мелкие размеры получаются масштабированием. ready(key) дёргает view на
перерисовку видимых ячеек.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QPixmap

from ydx.constants import PREVIEW_CONCURRENCY, THUMB_DIR
from ydx.core.models import Resource

log = logging.getLogger(__name__)

DISK_LIMIT = 512 * 1024 * 1024


def thumb_key(resource: Resource) -> str:
    base = resource.md5 or f"{resource.path}:{resource.modified or ''}"
    return hashlib.md5(base.encode("utf-8")).hexdigest()


class _FetchJob(QRunnable):
    def __init__(self, service: "ThumbService", key: str, url: str, px: int,
                 fetcher=None) -> None:
        super().__init__()
        self.service = service
        self.key = key
        self.url = url
        self.px = px
        self.fetcher = fetcher          # подмена загрузки (photounlim)

    def run(self) -> None:
        try:
            image = self.fetcher(self.url) if self.fetcher else self.service._download(self.url)
        except Exception as exc:  # noqa: BLE001
            log.debug("thumb fetch failed %s: %s", self.key, exc)
            self.service._mark_failed(self.key)
            return
        if image is None:
            self.service._mark_failed(self.key)
            return
        try:
            self.service._store(self.key, image)
        finally:
            self.service.ready.emit(self.key)


class ThumbService(QObject):
    """pixmap(resource, size) → из кэшей; мимо — ставит задачу загрузки."""

    ready = Signal(str)

    def __init__(self, api_provider, unlim_provider=None) -> None:
        # api_provider() -> ApiClient | None; unlim_provider() -> UnlimService | None
        super().__init__()
        self._api_provider = api_provider
        self._unlim_provider = unlim_provider
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(PREVIEW_CONCURRENCY)
        self._inflight: set[str] = set()
        self._failed: set[str] = set()
        self._mem: dict[str, QPixmap] = {}   # key → XL pixmap
        self._evict_if_needed()

    # --- публичное -------------------------------------------------------------

    def pixmap(self, resource: Resource, size: int) -> QPixmap | None:
        """Синхронно из кэшей; при промахе ставит фоновую загрузку."""
        if not resource.preview:
            return None
        key = thumb_key(resource)
        if key in self._failed:
            return None
        pm = self._mem.get(key)
        if pm is None:
            pm = self._load_disk(key)
        if pm is None:
            self._enqueue(resource, key)
            return None
        return pm.scaled(
            size, size, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    # --- загрузка ------------------------------------------------------------------

    def _enqueue(self, resource: Resource, key: str) -> None:
        if key in self._inflight:
            return
        self._inflight.add(key)
        fetcher = None
        if resource.path.startswith("photounlim:") and self._unlim_provider is not None:
            # превью безлимита отдают ТОЛЬКО с куками сессии (без них 403);
            # OAuth-заголовок тут бесполезен — сразу кукисессия, с запасным
            # вариантом через обычный клиент на случай смены поведения
            def fetcher(url=resource.preview):          # noqa: F811 — замыкание
                image = None
                unlim = self._unlim_provider()
                if unlim is not None:
                    try:
                        resp = unlim.fetch(url)
                        if resp.status_code == 200:
                            img = QImage()
                            if img.loadFromData(resp.content):
                                image = img
                    except Exception:                   # noqa: BLE001
                        image = None
                return image if image is not None else self._download(url)

        self._pool.start(_FetchJob(self, key, resource.preview, 800, fetcher))

    def _download(self, url: str) -> QImage | None:
        api = self._api_provider()
        if api is None:
            return None
        resp = api.raw_get(url, with_auth=True, timeout=30)
        if resp.status_code != 200:
            return None
        image = QImage()
        if not image.loadFromData(resp.content):
            return None
        return image

    def _store(self, key: str, image: QImage) -> None:
        pm = QPixmap.fromImage(image)
        self._mem[key] = pm
        if len(self._mem) > 600:          # мягкий лимит памяти
            for old in list(self._mem)[:100]:
                self._mem.pop(old, None)
        path = self._disk_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            pm.save(str(path), "PNG")
        except OSError as exc:
            log.debug("thumb save failed %s: %s", key, exc)

    def _mark_failed(self, key: str) -> None:
        self._failed.add(key)

    # --- дисковый кэш -----------------------------------------------------------------

    def _disk_path(self, key: str) -> Path:
        return THUMB_DIR / key[:2] / f"{key}.png"

    def _load_disk(self, key: str) -> QPixmap | None:
        path = self._disk_path(key)
        if not path.exists():
            return None
        pm = QPixmap(str(path))
        if not pm.isNull():
            self._mem[key] = pm
            return pm
        return None

    def _evict_if_needed(self) -> None:
        try:
            files = sorted(THUMB_DIR.rglob("*.png"), key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        total = sum(f.stat().st_size for f in files)
        if total <= DISK_LIMIT:
            return
        for f in files:
            if total <= DISK_LIMIT * 0.9:
                break
            try:
                total -= f.stat().st_size
                f.unlink()
            except OSError:
                pass
