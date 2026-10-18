"""Парсинг Resource/Quota, мусорные пути, форматирование."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ydx.core.models import Quota, Resource


def test_resource_from_api_full():
    r = Resource.from_api({
        "name": "тест 🎉 (2).txt",
        "type": "file",
        "path": "disk:/папка/тест 🎉 (2).txt",
        "size": 1048576,
        "modified": "2026-10-01T12:30:00+03:00",
        "md5": "abc",
        "mime_type": "text/plain",
        "media_type": "text",
        "preview": "https://x/preview",
        "public_url": "https://yadi.sk/d/XyZ",
        "public_key": "XyZ",
        "_embedded": {"total": 42},
    })
    assert r.name == "тест 🎉 (2).txt"
    assert r.modified == datetime(2026, 10, 1, 12, 30, tzinfo=timezone(timedelta(hours=3)))
    assert r.total == 42
    assert not r.is_dir


def test_resource_dir_and_trash_suffix():
    r = Resource.from_api({
        "name": "foo",
        "type": "dir",
        "path": "trash:/foo_1408546879",
        "origin_path": "disk:/важное/foo",
    })
    assert r.is_dir
    assert r.origin_path == "disk:/важное/foo"
    assert r.path.endswith("_1408546879")


def test_quota_free():
    q = Quota(total=100, used=30, trash_size=10)
    assert q.free == 70
    assert Quota().free == 0
