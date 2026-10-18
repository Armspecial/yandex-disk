"""Движок передач: очередь задач, 3 слота, пауза/отмена/повтор.

Карантин особенностей: upload-href без авторизации и живёт 30 минут;
после upload — пауза ~2 с перед следующими запросами (данные Яндекса
дозревают асинхронно, иначе можно получить 500).
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ydx.constants import TRANSFER_SLOTS, UPLOAD_SETTLE_DELAY
from ydx.core.api_resources import ResourcesApi
from ydx.core.errors import CancelledError, ConflictError
from ydx.core.models import TransferTask

CHUNK = 256 * 1024


class _UploadFile:
    """Файл-обёртка для requests.put(data=...): прогресс + пауза/отмена."""

    def __init__(self, fh, task: TransferTask, engine: "TransferEngine") -> None:
        self._fh = fh
        self._task = task
        self._engine = engine

    def read(self, size: int = -1) -> bytes:
        if self._task.state == "cancelled":
            raise CancelledError("cancelled")
        while self._task.state == "paused":
            time.sleep(0.2)
            if self._task.state == "cancelled":
                raise CancelledError("cancelled")
        data = self._fh.read(size)
        if data:
            self._engine._progress(self._task, len(data))
        return data


class TransferEngine(QObject):
    task_updated = Signal(int)       # id задачи (обновить строку)
    tasks_changed = Signal()         # состав очереди изменился
    batch_done = Signal()            # завершилась пачка (обновить листинг/квоту)

    def __init__(self, accounts) -> None:   # AccountManager
        super().__init__()
        self._accounts = accounts
        self._tasks: list[TransferTask] = []
        self._queue: deque[TransferTask] = deque()
        self._active: set[int] = set()
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._batch_pending = False

    # --- API ---------------------------------------------------------------------

    @property
    def tasks(self) -> list[TransferTask]:
        return list(self._tasks)

    def _resources(self) -> ResourcesApi:
        resources = self._accounts.resources
        if resources is None:
            raise RuntimeError("no account")
        return resources

    def enqueue_upload(self, local_paths: list[str], remote_dir: str) -> None:
        """Файлы и папки (рекурсивно) в remote_dir."""

        def job():
            jobs: list[tuple[Path, str]] = []
            for raw in local_paths:
                p = Path(raw)
                if p.is_dir():
                    for f in sorted(p.rglob("*")):
                        if f.is_file():
                            rel = f.relative_to(p)
                            jobs.append((f, f"{remote_dir.rstrip('/')}/{p.name}/{rel}"))
                else:
                    jobs.append((p, f"{remote_dir.rstrip('/')}/{p.name}"))
            return jobs

        def done(jobs: list[tuple[Path, str]]) -> None:
            from ydx.services.workers import run_in_thread

            # сначала недостающие папки (409 = уже есть, не ошибка)
            dirs = sorted({
                str(Path(remote)) .rsplit("/", 1)[0] for _, remote in jobs
            })
            run_in_thread(lambda: self._mkdirs(dirs), lambda _r: self._enqueue_files(jobs))

        from ydx.services.workers import run_in_thread

        self._batch_pending = True
        run_in_thread(job, done, lambda exc: self._batch_finished())

    def _mkdirs(self, dirs: list[str]) -> None:
        res = self._resources()
        made = set()
        for d in dirs:
            parts = [p for p in d.split("/") if p]
            acc = "disk:/"
            for part in parts:
                acc = acc.rstrip("/") + "/" + part
                if acc in made:
                    continue
                try:
                    res.mkdir(acc)
                except ConflictError:
                    pass
                made.add(acc)

    def _enqueue_files(self, jobs: list[tuple[Path, str]]) -> None:
        for path, remote in jobs:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            task = TransferTask(
                id=next(self._ids), kind="upload",
                local_path=str(path), remote_path=remote, size=size,
            )
            self._tasks.append(task)
            self._queue.append(task)
            self.task_updated.emit(task.id)
        self.tasks_changed.emit()
        self._kick()

    def enqueue_download(self, resource, target_dir: str) -> None:
        name = resource.name
        task = TransferTask(
            id=next(self._ids), kind="download",
            local_path=str(Path(target_dir) / name),
            remote_path=resource.path, size=resource.size, resource=resource,
        )
        self._tasks.append(task)
        self._queue.append(task)
        self.task_updated.emit(task.id)
        self.tasks_changed.emit()
        self._batch_pending = True
        self._kick()

    # --- управление задачей -----------------------------------------------------------

    def pause(self, task_id: int) -> None:
        for t in self._tasks:
            if t.id == task_id and t.state == "running":
                t.state = "paused"
                self.task_updated.emit(task_id)

    def resume(self, task_id: int) -> None:
        for t in self._tasks:
            if t.id == task_id and t.state == "paused":
                t.state = "running"
                self.task_updated.emit(task_id)

    def cancel(self, task_id: int) -> None:
        for t in self._tasks:
            if t.id == task_id and t.state in ("queued", "running", "paused"):
                t.state = "cancelled"
                t.error = None
                self.task_updated.emit(task_id)
                with self._lock:
                    self._active.discard(task_id)
                self._kick()

    def retry(self, task_id: int, overwrite: bool = False) -> None:
        for t in self._tasks:
            if t.id == task_id and t.state in ("error", "cancelled"):
                t.state = "queued"
                t.transferred = 0
                t.error = None
                t.overwrite = overwrite
                self._queue.append(t)
                self.task_updated.emit(task_id)
        self._kick()

    def clear_finished(self) -> None:
        self._tasks = [
            t for t in self._tasks if t.state in ("queued", "running", "paused")
        ]
        self.tasks_changed.emit()

    # --- исполнитель -----------------------------------------------------------------

    def _kick(self) -> None:
        from ydx.services.workers import run_in_thread

        while len(self._active) < TRANSFER_SLOTS and self._queue:
            task = self._queue.popleft()
            if task.state != "queued":
                continue
            self._active.add(task.id)
            task.state = "running"
            self.task_updated.emit(task.id)
            run_in_thread(
                lambda t=task: self._run_task(t),
                lambda _r, t=task: self._task_finished(t),
                lambda exc, t=task: self._task_failed(t, exc),
            )

    def _run_task(self, task: TransferTask) -> None:
        res = self._resources()
        if task.kind == "upload":
            href = res.upload_href(task.remote_path, overwrite=task.overwrite)
            with open(task.local_path, "rb") as fh:
                wrapper = _UploadFile(fh, task, self)
                resp = res.api.raw_put_file(href, wrapper, timeout=None)
                if resp.status_code >= 400:
                    raise RuntimeError(f"upload HTTP {resp.status_code}")
            time.sleep(UPLOAD_SETTLE_DELAY)
        else:
            href = res.download_href(task.remote_path)
            self._stream_download(task, href)

    def _stream_download(self, task: TransferTask, href: str) -> None:
        res = self._resources()
        resp = res.api.raw_get(href, with_auth=True, stream=True, timeout=300)
        if resp.status_code != 200:
            raise RuntimeError(f"download HTTP {resp.status_code}")
        target = Path(task.local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        total = int(resp.headers.get("Content-Length") or task.size or 0)
        done = 0
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=CHUNK):
                if task.state == "cancelled":
                    raise CancelledError("cancelled")
                while task.state == "paused":
                    time.sleep(0.2)
                    if task.state == "cancelled":
                        raise CancelledError("cancelled")
                if chunk:
                    fh.write(chunk)
                    done += len(chunk)
                    self._progress(task, len(chunk), force_total=total)
        tmp.replace(target)

    # --- сигналы/состояния ---------------------------------------------------------------

    def _progress(self, task: TransferTask, nbytes: int, force_total: int = 0) -> None:
        task.transferred += nbytes
        if force_total and not task.size:
            task.size = force_total
        self.task_updated.emit(task.id)

    def _task_finished(self, task: TransferTask) -> None:
        task.state = "done"
        with self._lock:
            self._active.discard(task.id)
        self.task_updated.emit(task.id)
        self._after_step()

    def _task_failed(self, task: TransferTask, exc: Exception) -> None:
        if isinstance(exc, CancelledError):
            task.state = "cancelled"
            self._cleanup_part(task)
        elif isinstance(exc, ConflictError):
            task.state = "conflict"
            task.error = "conflict"
        else:
            task.state = "error"
            task.error = str(exc)
        with self._lock:
            self._active.discard(task.id)
        self.task_updated.emit(task.id)
        self._after_step()

    @staticmethod
    def _cleanup_part(task: TransferTask) -> None:
        if task.kind == "download":
            part = Path(task.local_path + ".part")
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass

    def _after_step(self) -> None:
        self._kick()
        if not self._active and not self._queue:
            self._batch_finished()

    def _batch_finished(self) -> None:
        if self._batch_pending:
            self._batch_pending = False
            self.batch_done.emit()
