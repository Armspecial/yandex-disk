"""UnlimClient: конверт models-v2, скрейп sk, ретраи, маппинг bulk-info."""

from __future__ import annotations

import pytest
import responses as rsplib

from ydx.core.errors import UnlimAuthError, UnlimCaptchaError, UnlimError
from ydx.core.unlim_api import (
    UnlimClient,
    guess_media_type,
    map_resource_info,
    parse_cookie_string,
)

BASE = "https://disk.yandex.ru"
COOKIES = "yandex_uid=1; Session_id=3:abc; junk"

CLUSTERS_URL = f"{BASE}/models-v2?m=intapi/photo-get-clusters-with-resources"
BULK_URL = f"{BASE}/models-v2?m=mpfs/bulk-resource-info"


def _recorder():
    sleeps: list[float] = []
    return sleeps, sleeps.append


def _page_html(sk: str = "abc123") -> str:
    return f'<html>"sk":"{sk}""uid":42</html>'


def _cluster(cid: str, size: int, unlim: int = 0) -> dict:
    return {"id": cid, "size": size, "from": 1791388406000, "to": 1791388420000,
            "albums": {"photounlim": unlim, "camera": size - unlim}}


def _feed_item(path: str) -> dict:
    """Элемент ленты — почти пустой: только id/itemId/габариты (как в жизни)."""
    return {"id": path, "itemId": "2_00000_" + path[-6:], "width": 4000, "height": 3000}


def _info(path: str, **meta_over) -> dict:
    """Ответ mpfs/bulk-resource-info — полные данные элемента."""
    name = path.rsplit("/", 1)[-1]
    is_video = name.lower().endswith((".mp4", ".mov"))
    sizes = [
        {"name": "ORIGINAL", "url": "//1.downloader.disk.yandex.ru/disk/orig"},
        {"name": "XL", "url": "//2.downloader.disk.yandex.ru/preview/xl"},
        {"name": "M", "url": "//3.downloader.disk.yandex.ru/preview/m"},
    ]
    if is_video:
        sizes = sizes[1:]                       # у видео оригинала нет
    meta = {
        "mimetype": "video/mp4" if is_video else "image/jpeg",
        "size": 159548609 if is_video else 4111752,
        "etime": 1791388420,
        "sizes": sizes,
    }
    meta.update(meta_over)
    return {"id": path, "type": "file", "path": path, "name": name,
            "ctime": 1791399164, "mtime": 1791399164, "meta": meta}


def _client(sleeper) -> UnlimClient:
    return UnlimClient(COOKIES, sleeper=sleeper)


# --- parse_cookie_string ---------------------------------------------------------


def test_parse_cookie_string():
    assert parse_cookie_string(COOKIES) == {"yandex_uid": "1", "Session_id": "3:abc"}
    assert "junk" not in parse_cookie_string(COOKIES)    # сегмент без '=' пропущен
    assert parse_cookie_string("  Session_id=x  ") == {"Session_id": "x"}
    with pytest.raises(ValueError):
        parse_cookie_string("yandex_uid=1")              # нет Session_id
    with pytest.raises(ValueError):
        parse_cookie_string("")


# --- скрейп и конверт -------------------------------------------------------------


@rsplib.activate
def test_scrape_and_envelope():
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", body=_page_html(), content_type="text/html")
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-init-snapshot",
               json={"data": {"photoslice_id": "slice1"}})
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-get-snapshot",
               json={"data": {"items": []}})

    snapshot = _client(sleeper).snapshot()
    assert snapshot == ("slice1", [])

    init_req = rsplib.calls[1].request
    assert init_req.headers["X-Requested-With"] == "XMLHttpRequest"
    assert "Mozilla" in init_req.headers["User-Agent"]   # без UA диск даёт 302
    import json as jsonlib

    body = jsonlib.loads(init_req.body)
    assert body["sk"] == "abc123"
    assert body["apiMethod"] == "intapi/photo-init-snapshot"
    assert body["connection_id"].startswith("42")         # uid из HTML

    snap_req = rsplib.calls[2].request
    params = jsonlib.loads(snap_req.body)["requestParams"]
    assert params == {"id": "slice1", "locale": "ru"}


@rsplib.activate
def test_full_sequence_maps_items():
    """init → snapshot → clusters → bulk-info: полные Resource с превью/оригиналом."""
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", body=_page_html(), content_type="text/html")
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-init-snapshot",
               json={"data": {"photoslice_id": "slice1"}})
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-get-snapshot",
               json={"data": {"items": [
                   _cluster("2025-06", 3, unlim=2),
                   _cluster("2025-05", 10, unlim=0),      # без безлимита — не берём
               ]}})
    rsplib.add(rsplib.POST, CLUSTERS_URL, json={"data": {"clusters": {"fetched": [
        {"id": "2025-06", "items": [_feed_item("/photounlim/IMG_0001.jpg"),
                                    _feed_item("/disk/a.jpg"),      # не photounlim — мимо
                                    _feed_item("/photounlim/VID_0002.mp4")]},
    ]}}})
    rsplib.add(rsplib.POST, BULK_URL, json={"data": [
        _info("/photounlim/IMG_0001.jpg"),
        _info("/photounlim/VID_0002.mp4"),
    ]})

    items = list(_client(sleeper).iter_media())
    assert [r.path for r in items] == ["photounlim:/IMG_0001.jpg",
                                       "photounlim:/VID_0002.mp4"]

    import json as jsonlib

    clusters_req = [c for c in rsplib.calls
                    if "clusters-with-resources" in c.request.url][0].request
    params = jsonlib.loads(clusters_req.body)["requestParams"]
    assert params["photosliceId"] == "slice1"
    assert params["clusters"] == {"2025-06": {"range": [0, 2]}}
    assert params["hideScreenshots"] is False

    bulk_req = [c for c in rsplib.calls if "bulk-resource-info" in c.request.url][0].request
    assert jsonlib.loads(bulk_req.body)["requestParams"] == {
        "ids": ["/photounlim/IMG_0001.jpg", "/photounlim/VID_0002.mp4"]}

    photo = items[0]
    assert photo.media_type == "image"
    assert photo.size == 4111752
    assert photo.preview == "https://2.downloader.disk.yandex.ru/preview/xl"
    assert photo.file_url == "https://1.downloader.disk.yandex.ru/disk/orig"
    assert photo.modified is not None
    assert photo.modified.year == 2026 and photo.modified.tzinfo is not None

    video = items[1]
    assert video.media_type == "video"
    assert video.file_url is None                          # оригинала у видео нет
    assert video.preview is not None                       # но постер есть


@rsplib.activate
def test_cluster_chunking_and_sleeps():
    """120 кластеров → 3 запроса; между ними паузы 0.3–0.8 с."""
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", body=_page_html(), content_type="text/html")
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-init-snapshot",
               json={"data": {"photoslice_id": "s"}})
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-get-snapshot",
               json={"data": {"items": [_cluster(f"c{i}", 1, unlim=1)
                                         for i in range(120)]}})
    for _ in range(3):
        rsplib.add(rsplib.POST, CLUSTERS_URL,
                   json={"data": {"clusters": {"fetched": []}}})

    list(_client(sleeper).iter_media())
    cluster_calls = [c for c in rsplib.calls
                     if "clusters-with-resources" in c.request.url]
    assert len(cluster_calls) == 3
    assert sleeps == pytest.approx([0.3, 0.8], abs=0.5)    # две паузы между тремя


@rsplib.activate
def test_ckey_rescrape_and_retry():
    """CKEY → перескрестить sk и повторить тем же методом."""
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", body=_page_html("sk-old"),
               content_type="text/html")
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-init-snapshot",
               json={"data": {"photoslice_id": "s"}})
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-get-snapshot",
               json={"data": {"error": {"code": "CKEY"}}})
    rsplib.add(rsplib.GET, f"{BASE}/", body=_page_html("sk-new"),
               content_type="text/html")
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-get-snapshot",
               json={"data": {"items": []}})

    _client(sleeper).snapshot()

    import json as jsonlib

    retried = jsonlib.loads(rsplib.calls[4].request.body)
    assert retried["sk"] == "sk-new"


@rsplib.activate
def test_captcha_aborts():
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", body=_page_html(), content_type="text/html")
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-init-snapshot",
               json={"type": "captcha"})

    with pytest.raises(UnlimCaptchaError):
        _client(sleeper).count()
    assert len(rsplib.calls) == 2                          # без ретраев


@rsplib.activate
def test_backoff_on_429():
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", body=_page_html(), content_type="text/html")
    url = f"{BASE}/models-v2?m=intapi/photo-init-snapshot"
    rsplib.add(rsplib.POST, url, status=429, json={})
    rsplib.add(rsplib.POST, url, status=429, json={})
    rsplib.add(rsplib.POST, url, json={"data": {"photoslice_id": "s"}})

    client = _client(sleeper)
    init = client._call("intapi/photo-init-snapshot")
    assert init["photoslice_id"] == "s"
    assert sleeps == [1, 2]                                # BACKOFFS после 429


@rsplib.activate
def test_expired_session_redirect():
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", status=302,
               headers={"Location": "https://passport.yandex.ru/"})

    with pytest.raises(UnlimAuthError):
        _client(sleeper).snapshot()


@rsplib.activate
def test_no_sk_on_page():
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", body="<html>login please</html>",
               content_type="text/html")

    with pytest.raises(UnlimAuthError):
        _client(sleeper).snapshot()


@rsplib.activate
def test_count_sums_photounlim():
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", body=_page_html(), content_type="text/html")
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-init-snapshot",
               json={"data": {"photoslice_id": "s"}})
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-get-snapshot",
               json={"data": {"items": [_cluster("a", 5, unlim=3),
                                         _cluster("b", 2, unlim=4)]}})

    assert _client(sleeper).count() == 7


@rsplib.activate
def test_download_links_fresh_batch():
    """Свежие ORIGINAL-ссылки одним батчем; видео без ссылки не попадает."""
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", body=_page_html(), content_type="text/html")
    rsplib.add(rsplib.POST, BULK_URL, json={"data": [
        _info("/photounlim/a.jpg"),
        _info("/photounlim/v.mp4"),
    ]})

    links = _client(sleeper).download_links(["/photounlim/a.jpg", "/photounlim/v.mp4"])
    assert links == {"/photounlim/a.jpg":
                     "https://1.downloader.disk.yandex.ru/disk/orig"}


@rsplib.activate
def test_fetch_from_many_threads():
    """Миниатюры грузятся пулом потоков: клиент обязан пережить гонку."""
    import threading

    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00"
           b"\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\n"
           b"IDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00"
           b"\x00IEND\xaeB`\x82")
    rsplib.add(rsplib.GET, "https://cdn.test/p.jpg", body=png,
               content_type="image/png")

    client = UnlimClient(COOKIES)
    out = []
    errors = []

    def worker():
        try:
            r = client.fetch("https://cdn.test/p.jpg")
            out.append(r.status_code == 200 and r.content == png)
        except Exception as exc:                     # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and all(out)


# --- маппинг ---------------------------------------------------------------------


def test_map_resource_info_variants():
    # etime из meta приоритетнее ctime (время съёмки, не загрузки)
    r = map_resource_info(_info("/photounlim/photo heic.HEIC",
                                mimetype="image/heic", etime=1748772000,
                                ctime=1791399164))
    assert r.path == "photounlim:/photo heic.HEIC"
    assert r.media_type == "image"
    assert r.size == 4111752
    assert r.modified is not None and r.modified.year == 2025
    assert r.file_url.startswith("https://1.downloader")

    # пустая meta — даты/ссылок нет, но имя и тип есть
    r2 = map_resource_info({"id": "/photounlim/x.JPG", "name": "x.JPG"})
    assert r2.media_type == "image"                        # по расширению
    assert r2.preview is None and r2.file_url is None and r2.size == 0

    # не photounlim и мусор — None
    assert map_resource_info({"id": "/disk/x.jpg"}) is None
    assert map_resource_info({"name": "no id"}) is None


def test_guess_media_type():
    assert guess_media_type("video/mp4", "x.txt") == "video"
    assert guess_media_type("IMAGE/JPEG", "x") == "image"
    assert guess_media_type(None, "clip.MOV") == "video"
    assert guess_media_type(None, "archive.zip") is None


@rsplib.activate
def test_iter_media_filters_by_media_type():
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", body=_page_html(), content_type="text/html")
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-init-snapshot",
               json={"data": {"photoslice_id": "s"}})
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=intapi/photo-get-snapshot",
               json={"data": {"items": [_cluster("c", 2, unlim=2)]}})
    rsplib.add(rsplib.POST, CLUSTERS_URL, json={"data": {"clusters": {"fetched": [
        {"id": "c", "items": [_feed_item("/photounlim/IMG.jpg"),
                              _feed_item("/photounlim/VID.mp4")]},
    ]}}})
    rsplib.add(rsplib.POST, BULK_URL, json={"data": [
        _info("/photounlim/IMG.jpg"), _info("/photounlim/VID.mp4")]})

    videos = list(_client(sleeper).iter_media("video"))
    assert [v.name for v in videos] == ["VID.mp4"]


# --- mpfs/url: прямая ссылка (в т.ч. видео) ------------------------------------------


@rsplib.activate
def test_item_url_prefixes_scheme():
    """mpfs/url → .file: дописываем https: к протоколо-относительной ссылке."""
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", body=_page_html(), content_type="text/html")
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=mpfs/url",
               json={"data": {"file": "//downloader.disk.yandex.ru/disk/x?y=1"}})

    url = _client(sleeper).item_url("/photounlim/VID.MP4")
    assert url == "https://downloader.disk.yandex.ru/disk/x?y=1"

    import json as jsonlib

    body = jsonlib.loads(rsplib.calls[1].request.body)["requestParams"]
    assert body == {"path": "/photounlim/VID.MP4", "inline": True}


@rsplib.activate
def test_download_links_fallback_for_video():
    """Фото берёт ORIGINAL из bulk-инфо; видео (без ORIGINAL) — поштучно mpfs/url."""
    sleeps, sleeper = _recorder()
    rsplib.add(rsplib.GET, f"{BASE}/", body=_page_html(), content_type="text/html")
    rsplib.add(rsplib.POST, BULK_URL, json={"data": [
        _info("/photounlim/IMG.jpg"), _info("/photounlim/VID.mp4")]})
    rsplib.add(rsplib.POST, f"{BASE}/models-v2?m=mpfs/url",
               json={"data": {"file": "//downloader.disk.yandex.ru/disk/vid"}})

    links = _client(sleeper).download_links(
        ["/photounlim/IMG.jpg", "/photounlim/VID.mp4"])
    assert links == {
        "/photounlim/IMG.jpg": "https://1.downloader.disk.yandex.ru/disk/orig",
        "/photounlim/VID.mp4": "https://downloader.disk.yandex.ru/disk/vid",
    }


# --- удаление в корзину -------------------------------------------------------


def test_delete_resources_batches_and_echo(monkeypatch):
    """bulk-async-delete: пачки ≤40, payload [{src}], echo-ответ парсится."""
    from ydx.core.unlim_api import DELETE_BATCH, UnlimClient

    client = UnlimClient.__new__(UnlimClient)      # без сети
    calls = []

    def fake_call(method, params=None, **_k):
        calls.append((method, params))
        return [{"oid": f"o{i}", "operation": {"src": op["src"]}}
                for i, op in enumerate(params or [])]

    monkeypatch.setattr(client, "_call", fake_call)
    client._sleeper = lambda _s: None      # __new__: атрибута ещё нет
    assert DELETE_BATCH == 40

    paths = [f"/photounlim/f{i}.jpg" for i in range(45)]
    accepted = client.delete_resources(paths)

    assert [m for m, _p in calls] == ["mpfs/bulk-async-delete"] * 2
    assert calls[0][1] == [{"src": p} for p in paths[:40]]
    assert len(calls[1][1]) == 5
    assert accepted == paths                       # echo вернул все пути


def test_delete_resources_without_echo_accepts_batch(monkeypatch):
    """Ответ без списка операций (форма изменилась) — пачка принята целиком."""
    from ydx.core.unlim_api import UnlimClient

    client = UnlimClient.__new__(UnlimClient)
    monkeypatch.setattr(client, "_call", lambda _m, _p=None, **_k: {})
    accepted = client.delete_resources(["/photounlim/one.jpg"])
    assert accepted == ["/photounlim/one.jpg"]
