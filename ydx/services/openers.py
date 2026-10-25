"""Открытие файла двойным кликом: скачивание в кэш → системный просмотрщик."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget

from ydx.constants import OPEN_SIZE_LIMIT
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
    # через модуль (не from-import): каталог должен поддаваться подмене в тестах
    from ydx import constants as const

    return const.OPEN_CACHE_DIR / f"{thumb_key(resource)}-{_safe_name(resource.name)}"


class DownloadCancelled(Exception):
    """Загрузка отменена (окно закрыли)."""


def _stream_to_file(resp, target: Path, progress=None,
                    stall_timeout: float = 20.0, abort=None) -> Path:
    """Ответ со stream=True → файл; progress(байт_скачано, всего_из_Content-Length).

    Узлы хранения иногда «дозируют» байты до полной остановки — читный
    таймаут при этом не стреляет (пакеты идут чаще таймаута). Сторож
    облипания обрывает ответ, если байт нет stall_timeout секунд; abort
    (threading.Event) закрывает загрузку немедленно — окно закрыли.
    """
    import threading
    import time

    total = int(resp.headers.get("Content-Length") or 0)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    last = [time.time()]
    stop = threading.Event()

    def watchdog():
        while not stop.wait(1.0):
            stalled = time.time() - last[0] > stall_timeout
            cancelled = abort is not None and abort.is_set()
            if stalled or cancelled:
                try:
                    resp.close()              # обрывает iter_content
                except Exception:             # noqa: BLE001 — обрываем чтение
                    pass
                return

    watcher = threading.Thread(target=watchdog, daemon=True)
    watcher.start()
    done = 0
    try:
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if chunk:
                    fh.write(chunk)
                    done += len(chunk)
                    last[0] = time.time()
                    if progress is not None:
                        progress(done, total)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        stop.set()
        watcher.join(timeout=2)
    if abort is not None and abort.is_set():
        tmp.unlink(missing_ok=True)
        raise DownloadCancelled("download cancelled")
    if total and done < total:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"download stalled: {done} of {total} bytes")
    tmp.replace(target)
    return target


def _download(resources: ResourcesApi, resource: Resource, target: Path,
              progress=None, abort=None) -> Path:
    if target.exists() and target.stat().st_size == resource.size:
        return target          # уже в кэше
    target.parent.mkdir(parents=True, exist_ok=True)
    href = resources.download_href(resource.path)
    api = resources.api
    resp = api.raw_get(href, with_auth=True, stream=True, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(f"download failed: {resp.status_code}")
    return _stream_to_file(resp, target, progress, abort=abort)


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
                   progress=None, abort=None) -> dict:
    """Скачать элементы безлимита по прямым ссылкам (кукисессией).

    Ссылки короткоживущие — перед скачиванием берём свежий пакет
    (один bulk-запрос на всю выборку). Узлы хранения капризные: при обрыве
    или залипании делается вторая попытка со свежей ссылкой (может уйти
    на другой узел). Блокирующая — вызывающий обязан запустить в потоке.
    progress(путь, скачано, всего) — для просмотрщика. abort — отмена.
    Возвращает {"done", "skipped", "names", "paths", "errors"}.
    """
    done = 0
    skipped: list[str] = []
    saved: list[str] = []
    errors: dict[str, str] = {}
    target_dir.mkdir(parents=True, exist_ok=True)
    links = unlim.download_links(resources)
    for resource in resources:
        target = target_dir / _safe_name(resource.name)
        urls = [links.get(resource.path) or resource.file_url]
        while urls and urls[-1]:
            try:
                resp = unlim.fetch(urls[-1], stream=True)
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP {resp.status_code}")
                _stream_to_file(
                    resp, target,
                    lambda d, t, _r=resource: progress(_r.path, d, t)
                    if progress else None,
                    abort=abort)
                done += 1
                saved.append(str(target))
                errors.pop(resource.path, None)
                break
            except DownloadCancelled:
                raise
            except Exception as exc:                      # noqa: BLE001 — пачка важнее
                retry = unlim.item_url(resource.path) if urls[-1] else None
                if retry and retry != urls[-1] and not (abort and abort.is_set()):
                    urls.append(retry)          # ещё попытка, возможно другой узел
                    continue
                skipped.append(resource.name)
                errors[resource.path] = str(exc)
                break
        else:
            skipped.append(resource.name)       # ссылки не было вовсе
            errors.setdefault(resource.path, "no link")
    return {"done": done, "skipped": len(skipped), "names": skipped,
            "paths": saved, "errors": errors}
