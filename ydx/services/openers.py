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


def _stream_to_file(resp, target: Path, progress=None) -> Path:
    """Ответ со stream=True → файл; progress(байт_скачано, всего_из_Content-Length)."""
    total = int(resp.headers.get("Content-Length") or 0)
    tmp = target.with_suffix(target.suffix + ".part")
    done = 0
    with open(tmp, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if chunk:
                fh.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    tmp.replace(target)
    return target


def _download(resources: ResourcesApi, resource: Resource, target: Path,
              progress=None) -> Path:
    if target.exists() and target.stat().st_size == resource.size:
        return target          # уже в кэше
    target.parent.mkdir(parents=True, exist_ok=True)
    href = resources.download_href(resource.path)
    api = resources.api
    resp = api.raw_get(href, with_auth=True, stream=True, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(f"download failed: {resp.status_code}")
    return _stream_to_file(resp, target, progress)


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


def unlim_download(unlim, resources: list[Resource], target_dir: Path,
                   progress=None) -> dict:
    """Скачать элементы безлимита по прямым ссылкам (кукисессией).

    Ссылки короткоживущие — перед скачиванием берём свежий пакет
    (один bulk-запрос на всю выборку). Блокирующая — вызывающий обязан
    запустить в потоке. progress(путь, скачано, всего) — для просмотрщика.
    Возвращает {"done", "skipped", "names"}: без ссылки (видео пока)
    элемент пропускается, ошибки сети тоже уводят в skipped, не роняя пачку.
    """
    done = 0
    skipped: list[str] = []
    target_dir.mkdir(parents=True, exist_ok=True)
    links = unlim.download_links(resources)
    for resource in resources:
        url = links.get(resource.path) or resource.file_url
        if not url:
            skipped.append(resource.name)
            continue
        target = target_dir / _safe_name(resource.name)
        try:
            resp = unlim.fetch(url, stream=True)
            if resp.status_code != 200:
                skipped.append(resource.name)
                continue
            _stream_to_file(resp, target, lambda d, t, _r=resource:
                            progress(_r.path, d, t) if progress else None)
            done += 1
        except Exception:                                  # noqa: BLE001 — пачка важнее
            skipped.append(resource.name)
    return {"done": done, "skipped": len(skipped), "names": skipped}
