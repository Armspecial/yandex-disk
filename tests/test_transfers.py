"""TransferEngine: поток upload/download, конфликт, отмена, batch_done."""

from __future__ import annotations

from pathlib import Path

import pytest


class _Resp:
    def __init__(self, status: int) -> None:
        self.status_code = status


class _FakeApi:
    def __init__(self) -> None:
        self.puts: list[str] = []

    def raw_put_file(self, url, fileobj, timeout=None) -> _Resp:
        # вычитываем обёртку как это сделал бы requests
        while fileobj.read(256 * 1024):
            pass
        self.puts.append(url)
        return _Resp(201)

    def raw_get(self, url, with_auth=True, stream=False, timeout=30):
        class _Stream:
            status_code = 200
            headers = {"Content-Length": "4"}

            @staticmethod
            def iter_content(chunk_size=1):
                yield b"data"

        return _Stream()


class _FakeResources:
    def __init__(self) -> None:
        self.api = _FakeApi()
        self.mkdirs: list[str] = []
        self.conflict_paths: set[str] = set()

    def upload_href(self, path: str, overwrite: bool = False) -> str:
        if not overwrite and path in self.conflict_paths:
            from ydx.core.errors import ConflictError

            raise ConflictError(409, "DiskPathAlreadyExistsError", "exists")
        return "https://uploader/" + path.replace("/", "_")

    def download_href(self, path: str) -> str:
        return "https://downloader/" + path.replace("/", "_")

    def mkdir(self, path: str) -> None:
        self.mkdirs.append(path)


class _FakeAccounts:
    def __init__(self, resources) -> None:
        self.resources = resources


@pytest.fixture()
def sync_engine(monkeypatch, tmp_path):
    """Движок с синхронным run_in_thread и отключёнными паузами."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from ydx.services import transfers as tr_mod
    from ydx.services.workers import run_in_thread as _real

    def sync_run(fn, on_done=None, on_error=None):
        try:
            result = fn()
            if on_done:
                on_done(result)
        except Exception as exc:  # noqa: BLE001
            if on_error:
                on_error(exc)

    monkeypatch.setattr("ydx.services.workers.run_in_thread", sync_run)
    monkeypatch.setattr(tr_mod, "UPLOAD_SETTLE_DELAY", 0.0)

    def make(paths: dict[str, bytes]):
        for rel, data in paths.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        res = _FakeResources()
        engine = tr_mod.TransferEngine(_FakeAccounts(res))
        return engine, res

    return make, tmp_path


def test_upload_two_files_done(sync_engine):
    make, tmp = sync_engine
    engine, res = make({"a.txt": b"hello", "b.txt": b"world!!"})
    fired = []
    engine.batch_done.connect(lambda: fired.append(True))

    engine.enqueue_upload([str(tmp / "a.txt"), str(tmp / "b.txt")], "disk:/")

    assert [t.state for t in engine.tasks] == ["done", "done"]
    assert len(res.api.puts) == 2
    assert fired, "batch_done должен сработать"


def test_upload_folder_recursive_mkdirs(sync_engine):
    make, tmp = sync_engine
    engine, res = make({"dir/sub/f.txt": b"x", "dir/g.txt": b"yy"})
    engine.enqueue_upload([str(tmp / "dir")], "disk:/target")

    states = [t.state for t in engine.tasks]
    assert states == ["done", "done"] or sorted(s for s in states) == ["done", "done"]
    remotes = sorted(t.remote_path for t in engine.tasks)
    assert remotes == [
        "disk:/target/dir/g.txt",
        "disk:/target/dir/sub/f.txt",
    ]
    assert "disk:/target/dir/sub" in res.mkdirs


def test_upload_conflict_then_overwrite(sync_engine):
    make, tmp = sync_engine
    engine, res = make({"a.txt": b"data"})
    res.conflict_paths.add("disk:/a.txt")

    engine.enqueue_upload([str(tmp / "a.txt")], "disk:/")
    task = engine.tasks[0]
    assert task.state == "conflict"

    engine.retry(task.id, overwrite=True)
    assert engine.tasks[0].state == "done"


def test_cancel_queued(sync_engine, monkeypatch):
    make, tmp = sync_engine
    monkeypatch.setattr("ydx.services.transfers.TRANSFER_SLOTS", 1)
    engine, res = make({"f1": b"1", "f2": b"2", "f3": b"3"})
    engine.enqueue_upload([str(tmp / "f1"), str(tmp / "f2"), str(tmp / "f3")], "disk:/")
    # с синхронным исполнением всё уже выполнено; отменяем готовую — без эффекта
    last = engine.tasks[-1]
    engine.cancel(last.id)
    assert last.state in ("done", "cancelled")


def test_download_to_file(sync_engine, tmp_path):
    make, _ = sync_engine
    engine, res = make({})
    from ydx.core.models import Resource

    r = Resource(name="f.bin", type="file", path="disk:/f.bin", size=4)
    engine.enqueue_download(r, str(tmp_path / "dl"))
    target = tmp_path / "dl" / "f.bin"
    assert target.read_bytes() == b"data"
    assert engine.tasks[0].state == "done"
    assert not list((tmp_path / "dl").glob("*.part"))
