"""Ресурсы API: пагинация, 201-vs-202, мусорные операции (на записанных ответах)."""

from __future__ import annotations

import pytest
import responses as rsplib

from ydx.core import api_resources
from ydx.core.api_client import ApiClient, StaticTokenProvider
from ydx.core.api_resources import ResourcesApi

BASE = "https://cloud-api.yandex.net/v1/disk"


@pytest.fixture(autouse=True)
def _single_walk_worker(monkeypatch):
    """Моки responses срабатывают по порядку — обход дерева в один поток."""
    monkeypatch.setattr(api_resources, "WALK_WORKERS", 1)


def _api():
    return ResourcesApi(ApiClient(StaticTokenProvider("t")))


def _page(items, total, path):
    return {
        "name": path.split("/")[-1] or "disk",
        "type": "dir",
        "path": path,
        "_embedded": {"items": items, "total": total, "limit": 200, "offset": 0},
    }


@rsplib.activate
def test_iter_dir_pagination():
    api = _api()
    items = [{"name": f"f{i}.txt", "type": "file", "path": f"disk:/f{i}.txt"} for i in range(5)]
    rsplib.add(rsplib.GET, f"{BASE}/resources", json=_page(items[:3], 5, "disk:/p"))
    rsplib.add(rsplib.GET, f"{BASE}/resources", json=_page(items[3:], 5, "disk:/p"))
    got = list(api.iter_dir("disk:/p", page=3))
    assert [r.name for r in got] == [f"f{i}.txt" for i in range(5)]


@rsplib.activate
def test_copy_201_done_no_operation():
    api = _api()
    rsplib.add(rsplib.POST, f"{BASE}/resources/copy", status=201,
               json={"href": "https://cloud-api.yandex.net/v1/disk/resources?path=disk:/b", "method": "GET"})
    assert api.copy("disk:/a", "disk:/b") is None  # Link указывает на ресурс, не на операцию


@rsplib.activate
def test_move_202_returns_operation():
    api = _api()
    rsplib.add(rsplib.POST, f"{BASE}/resources/move", status=202,
               json={"href": f"{BASE}/operations?id=op1", "method": "GET"})
    op = api.move("disk:/a", "disk:/b")
    assert op is not None and "op1" in op.href


@rsplib.activate
def test_delete_204_none():
    api = _api()
    rsplib.add(rsplib.DELETE, f"{BASE}/resources", status=204)
    assert api.delete("disk:/a") is None


@rsplib.activate
def test_trash_delete_without_path_202():
    api = _api()
    rsplib.add(rsplib.DELETE, f"{BASE}/trash/resources", status=202,
               json={"href": f"{BASE}/operations?id=op2", "method": "GET"})
    op = api.trash_delete(None)
    assert op is not None and "op2" in op.href
    assert "path" not in rsplib.calls[0].request.url  # path опущен = очистить всё


@rsplib.activate
def test_list_public_total_fallback():
    api = _api()
    rsplib.add(rsplib.GET, f"{BASE}/resources/public",
               json={"items": [{"name": "a", "type": "file", "path": "disk:/a"}], "limit": 200, "offset": 0})
    items, marker = api.list_public(limit=200)
    assert len(items) == 1
    assert marker is None


@rsplib.activate
def test_iter_media_walks_tree_with_photostream_first():
    """Фото = автозагрузка (новыми вперёд) + обход дерева; дубли и чужой media_type
    отбрасываются, папка автозагрузки повторно не листается."""
    api = _api()
    rsplib.add(rsplib.GET, f"{BASE}/", json={
        "total_space": 1, "used_space": 0,
        "system_folders": {"photostream": "disk:/Camera Uploads"},
    })
    # 1) автозагрузка: видео отфильтруется
    stream_items = [
        {"name": "v.MP4", "type": "file", "path": "disk:/Camera Uploads/v.MP4",
         "media_type": "video"},
        {"name": "p1.JPG", "type": "file", "path": "disk:/Camera Uploads/p1.JPG",
         "media_type": "image"},
    ]
    rsplib.add(rsplib.GET, f"{BASE}/resources",
               json=_page(stream_items, 2, "disk:/Camera Uploads"))
    # 2) корень: папки (включая автозагрузку — её скипаем) + файл в корне
    root_items = [
        {"name": "Camera Uploads", "type": "dir", "path": "disk:/Camera Uploads"},
        {"name": "Screenshots", "type": "dir", "path": "disk:/Screenshots"},
        {"name": "root.jpg", "type": "file", "path": "disk:/root.jpg",
         "media_type": "image"},
    ]
    rsplib.add(rsplib.GET, f"{BASE}/resources", json=_page(root_items, 3, "disk:"))
    # 3) вложенная папка: дубль из автозагрузки не пройдёт
    sub_items = [
        {"name": "p1.JPG", "type": "file", "path": "disk:/Camera Uploads/p1.JPG",
         "media_type": "image"},
        {"name": "shot.png", "type": "file", "path": "disk:/Screenshots/shot.png",
         "media_type": "image"},
    ]
    rsplib.add(rsplib.GET, f"{BASE}/resources",
               json=_page(sub_items, 2, "disk:/Screenshots"))
    got = [r.path for r in api.iter_media("image")]
    assert got == ["disk:/Camera Uploads/p1.JPG", "disk:/root.jpg",
                   "disk:/Screenshots/shot.png"]
    # повторный листинг автозагрузки не запрашивался: ровно 3 запроса /resources
    assert sum(1 for c in rsplib.calls if "/resources" in c.request.url
               and "/resources/" not in c.request.url) == 3


@rsplib.activate
def test_iter_media_survives_disk_info_and_folder_failures():
    """disk_info недоступен и папка падает → обход продолжается, генератор жив."""
    api = _api()
    root_items = [
        {"name": "broken", "type": "dir", "path": "disk:/broken"},
        {"name": "ok", "type": "dir", "path": "disk:/ok"},
    ]
    rsplib.add(rsplib.GET, f"{BASE}/resources", json=_page(root_items, 2, "disk:"))
    rsplib.add(rsplib.GET, f"{BASE}/resources", status=404)   # disk:/broken
    rsplib.add(rsplib.GET, f"{BASE}/resources",
               json=_page([{"name": "a.jpg", "type": "file", "path": "disk:/ok/a.jpg",
                            "media_type": "image"}], 1, "disk:/ok"))
    got = list(api.iter_media("image"))
    assert [r.path for r in got] == ["disk:/ok/a.jpg"]
