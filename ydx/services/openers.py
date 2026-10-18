"""Открытие файла двойным кликом: скачивание в кэш → системный просмотрщик."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget

from ydx.constants import OPEN_CACHE_DIR, OPEN_SIZE_LIMIT
from ydx.core.api_resources import ResourcesApi
from ydx.core.models import Resource
from ydx.services import i18n
from ydx.services.fmt import human_size
from ydx.services.thumbs import thumb_key
from ydx.services.workers import run_in_thread
from ydx.ui.errors_ui import confirm, show_api_error


def _safe_name(name: str) -> str:
    return re.sub(r"/", "_", name)[:120] or "file"


def cache_path(resource: Resource) -> Path:
    return OPEN_CACHE_DIR / f"{thumb_key(resource)}-{_safe_name(resource.name)}"


def _download(resources: ResourcesApi, resource: Resource, target: Path) -> Path:
    if target.exists() and target.stat().st_size == resource.size:
        return target          # уже в кэше
    target.parent.mkdir(parents=True, exist_ok=True)
    href = resources.download_href(resource.path)
    api = resources.api
    resp = api.raw_get(href, with_auth=True, stream=True, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(f"download failed: {resp.status_code}")
    tmp = target.with_suffix(target.suffix + ".part")
    with open(tmp, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if chunk:
                fh.write(chunk)
    tmp.replace(target)
    return target


def open_resource(
    parent: QWidget | None, resources: ResourcesApi, resource: Resource,
) -> None:
    if resource.size > OPEN_SIZE_LIMIT:
        if not confirm(
            parent,
            i18n.tr("files.open_too_big",
                    size=human_size(resource.size), limit=human_size(OPEN_SIZE_LIMIT)),
        ):
            return

    def done(path: Path) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    run_in_thread(
        lambda: _download(resources, resource, cache_path(resource)),
        done,
        lambda exc: show_api_error(parent, exc, i18n.tr("files.open_failed")),
    )
