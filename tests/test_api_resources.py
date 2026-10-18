"""Ресурсы API: пагинация, 201-vs-202, мусорные операции (на записанных ответах)."""

from __future__ import annotations

import responses as rsplib

from ydx.core.api_client import ApiClient, StaticTokenProvider
from ydx.core.api_resources import ResourcesApi

BASE = "https://cloud-api.yandex.net/v1/disk"


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
