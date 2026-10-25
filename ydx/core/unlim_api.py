"""Раздел «Безлимитная автозагрузка»: доступ к ленте, невидимой REST API.

Реализация работает поверх приватного транспорта веб-клиента (models-v2,
авторизация куками пользовательской браузерной сессии — их вводит сам
пользователь). Контракт не публичный; особенности поведения собраны здесь,
чтобы жить в одном файле:

- конверт запроса: {"sk", "connection_id": "<uid><millis>", "apiMethod",
  "requestParams"} при том же методе в query-параметре m;
- БЕЗ браузерного User-Agent сервер отвечает 302 без страницы и без sk —
  это НЕ истёкшие куки, хотя выглядело так же;
- sk (CSRF) и uid скребутся из HTML главной; при data.error.code == "CKEY"
  sk протухает — перескрестить и повторить; type == "captcha" — стоп;
- цепочка ленты: photo-init-snapshot → photoslice_id; photo-get-snapshot →
  кластеры {id, size, from/to (epoch-ms), albums.photounlim};
  photo-get-clusters-with-resources → элементы только с id/itemId (чанки
  по 50 кластеров);
- ПОЛНЫЕ данные (даты/размер/mimetype/превью/оригинал) элемента отдаёт
  mpfs/bulk-resource-info по ПУТЯМ ("/photounlim/…"), батчами по 50:
  meta.sizes[] с url вида "//N.downloader.disk.yandex.ru/…" (протокол
  дописываем); ORIGINAL = файл целиком (есть у фото, НЕТ у видео);
  превью требуют кук, оригинал качается без них; часть узлов хранения
  молчит до таймаута — ретраи обязательны;
- etime — время съёмки (EXIF), ctime/mtime — загрузки; берём etime;
- сервер жёстко лимитирует частоту (429 после серии запросов) — паузы
  между батчами обязательны.
"""

from __future__ import annotations

import random
import re
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timezone

import requests

from ydx.core.errors import UnlimAuthError, UnlimCaptchaError, UnlimError
from ydx.core.models import Resource

WEB_BASE = "https://disk.yandex.ru"
PHOTOS_URL = f"{WEB_BASE}/photos"    # веб-раздел «Фото» — источник кук для вставки

# Без браузерного UA диск отдаёт 302 без страницы (и без sk) — это не «истёкшие
# куки», хотя выглядело так же. Представляемся десктопным Chromium.
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CLUSTER_CHUNK = 50                 # кластеров на запрос (лимит сервера)
INFO_BATCH = 50                    # путей на mpfs/bulk-resource-info
DELETE_BATCH = 40                  # операций на mpfs/bulk-async-delete (выше — Too many items)
CHUNK_SLEEP = (0.3, 0.8)           # пауза между чанками, с (джиттер)
BACKOFFS = (1, 2, 4, 8)            # ретраи 429/5xx — как ApiClient

_SK_RE = re.compile(r'"sk":"([^"]+)"')
_UID_RE = re.compile(r'"uid":"?(\d+)')
_PHOTO_PREFIX = "/photounlim/"

# порядок предпочтения превью: тайл сетки и просмотрщик (плюс постер видео)
_PREVIEW_ORDER = ("XL", "XXL", "L", "M", "XXXL", "S", "DEFAULT")

_VIDEO_EXT = {".mp4", ".mov", ".avi", ".3gp", ".mkv", ".webm", ".m4v", ".hevc"}
_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".gif",
              ".bmp", ".tiff", ".dng"}


def parse_cookie_string(raw: str) -> dict[str, str]:
    """Строка Cookie-заголовка → словарь. Без Session_id — ValueError."""
    cookies: dict[str, str] = {}
    for part in (raw or "").split(";"):
        if "=" not in part:
            continue
        key, _, value = part.strip().partition("=")
        key = key.strip()
        if key:
            cookies[key] = value.strip()
    if "Session_id" not in cookies:
        raise ValueError("Session_id cookie missing")
    return cookies


def _parse_unlim_dt(value) -> datetime | None:
    """ISO (Z/смещение) или epoch сек/мс → tz-aware datetime; иначе None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:            # миллисекунды
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                return _parse_unlim_dt(float(value))
            except ValueError:
                return None
    return None


def guess_media_type(mime: str | None, name: str) -> str | None:
    """mimetype → image/video; fallback по расширению имени."""
    if mime:
        mime = mime.lower()
        if mime.startswith("video/"):
            return "video"
        if mime.startswith("image/"):
            return "image"
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in _VIDEO_EXT:
        return "video"
    if ext in _IMAGE_EXT:
        return "image"
    return None


def _sizes_by_name(meta: dict) -> dict[str, str]:
    """meta.sizes[] → {имя: url} с дописанным https:."""
    out: dict[str, str] = {}
    for size in meta.get("sizes") or []:
        name = size.get("name")
        url = size.get("url")
        if isinstance(name, str) and isinstance(url, str) and url:
            out[name] = "https:" + url if url.startswith("//") else url
    return out


def _pick_preview(sizes: dict[str, str]) -> str | None:
    for name in _PREVIEW_ORDER:
        if name in sizes:
            return sizes[name]
    return next(iter(sizes.values()), None)


def map_resource_info(info: dict) -> Resource | None:
    """Ответ mpfs/bulk-resource-info → Resource (или None для мусора)."""
    path = info.get("id") or info.get("path")
    if not isinstance(path, str) or not path.startswith(_PHOTO_PREFIX):
        return None
    meta = info.get("meta") or {}
    sizes = _sizes_by_name(meta)
    name = info.get("name") or path.rsplit("/", 1)[-1]
    when = (_parse_unlim_dt(meta.get("etime"))
            or _parse_unlim_dt(info.get("etime"))
            or _parse_unlim_dt(info.get("mtime"))
            or _parse_unlim_dt(info.get("ctime")))
    mime = meta.get("mimetype") or meta.get("mime")
    return Resource(
        name=name,
        type="file",
        path="photounlim:/" + name,
        size=int(meta.get("size") or 0),
        created=when,
        modified=when,
        mime_type=mime if isinstance(mime, str) else None,
        media_type=guess_media_type(mime if isinstance(mime, str) else None, name),
        preview=_pick_preview(sizes),
        file_url=sizes.get("ORIGINAL"),      # есть у фото; у видео — None
    )


class UnlimClient:
    """Клиент internal web-API: куки браузерной сессии, без OAuth."""

    def __init__(self, cookies: str | dict[str, str], base_url: str = WEB_BASE,
                 sleeper=None, timeout: int = 30) -> None:
        self._base = base_url.rstrip("/")
        self._sleeper = sleeper or time.sleep
        self._timeout = timeout
        self._sk: str | None = None
        self._uid: str | None = None
        # requests.Session не потокобезопасен, а клиент зовут из пула
        # миниатюр, обхода ленты и перегруппировки дат одновременно
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
        })
        parsed = cookies if isinstance(cookies, dict) else parse_cookie_string(cookies)
        self._session.cookies.update(parsed)

    # --- транспорт -----------------------------------------------------------

    def _scrape(self) -> None:
        """sk/uid из HTML главной; сессия без куки уводит на паспорт."""
        with self._lock:
            resp = self._session.get(self._base + "/", timeout=self._timeout,
                                     allow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            raise UnlimAuthError("session redirected to "
                                 + resp.headers.get("Location", "?"))
        match = _SK_RE.search(resp.text or "")
        if match is None:
            raise UnlimAuthError("sk not found on page")
        self._sk = match.group(1)
        uid = _UID_RE.search(resp.text or "")
        self._uid = uid.group(1) if uid else ""

    def _post(self, method: str, payload: dict) -> requests.Response:
        with self._lock:
            return self._session.post(
                f"{self._base}/models-v2",
                params={"m": method},
                json=payload,
                timeout=self._timeout,
            )

    def _call(self, method: str, request_params: dict | None = None,
              _rescrape: bool = True) -> dict:
        """POST /models-v2 с конвертом; ретраи, captcha, CKEY-перескрейп."""
        if self._sk is None:
            self._scrape()
        for attempt, backoff in enumerate((None, *BACKOFFS)):
            if backoff is not None:
                self._sleeper(backoff)
            payload = {
                "sk": self._sk,
                "connection_id": f"{self._uid}{int(time.time() * 1000)}",
                "apiMethod": method,
            }
            if request_params is not None:
                payload["requestParams"] = request_params
            try:
                resp = self._post(method, payload)
            except requests.RequestException as exc:
                raise UnlimError(f"{method}: {exc}") from exc
            if resp.status_code == 200:
                body = _json_body(resp)
                if body.get("type") == "captcha":
                    raise UnlimCaptchaError(f"{method}: captcha required")
                data = body.get("data") if isinstance(body.get("data"), dict) else body
                error = (data.get("error") or {}) if isinstance(data, dict) else {}
                if error.get("code") == "CKEY" and _rescrape:
                    self._sk = None
                    self._scrape()
                    return self._call(method, request_params, _rescrape=False)
                return data
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == len(BACKOFFS):        # последняя попытка
                    raise UnlimError(f"{method}: HTTP {resp.status_code}")
                continue
            raise UnlimError(f"{method}: HTTP {resp.status_code}")
        raise UnlimError(f"{method}: retries exhausted")   # pragma: no cover

    # --- сценарий photounlim ---------------------------------------------------

    def snapshot(self) -> tuple[str, list[dict]]:
        """(photoslice_id, кластеры с безлимитом)."""
        init = self._call("intapi/photo-init-snapshot")
        slice_id = init.get("photoslice_id") if isinstance(init, dict) else None
        if not slice_id:
            raise UnlimError("photo-init-snapshot: no photoslice_id")
        snap = self._call("intapi/photo-get-snapshot",
                          {"id": slice_id, "locale": "ru"})
        clusters = [c for c in (snap.get("items") or [])
                    if (c.get("albums") or {}).get("photounlim", 0) > 0]
        return str(slice_id), clusters

    def count(self) -> int:
        """Сколько элементов безлимита (для «Проверить»)."""
        _, clusters = self.snapshot()
        return sum(c["albums"]["photounlim"] for c in clusters)

    def _cluster_paths(self, slice_id: str, clusters: list[dict]) -> dict[str, list[str]]:
        """Пути /photounlim/… по кластерам: {id кластера: [пути]}. Элементы
        отдаются почти пустыми (полные метаданные — bulk_info)."""
        params = {
            "photosliceId": slice_id,
            "clusters": {
                c["id"]: {"range": [0, max(int(c.get("size") or 1) - 1, 0)]}
                for c in clusters
            },
            "hideScreenshots": False,
        }
        data = self._call("intapi/photo-get-clusters-with-resources", params)
        fetched = ((data.get("clusters") or {}).get("fetched")
                   if isinstance(data, dict) else None) or []
        out: dict[str, list[str]] = {}
        for cluster in fetched:
            cid = cluster.get("id")
            if not isinstance(cid, str):
                continue
            out[cid] = [
                item.get("id") for item in cluster.get("items") or []
                if isinstance(item.get("id"), str)
                and item.get("id").startswith(_PHOTO_PREFIX)
            ]
        return out

    def bulk_info(self, paths: list[str]) -> dict[str, dict]:
        """Полные метаданные путей: {путь: ответ bulk-resource-info}."""
        out: dict[str, dict] = {}
        for start in range(0, len(paths), INFO_BATCH):
            batch = paths[start:start + INFO_BATCH]
            payload = self._call("mpfs/bulk-resource-info", {"ids": batch})
            items = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                items = (payload or {}).get("resources") or []
            for info in items:
                if isinstance(info, dict) and info.get("id"):
                    out[info["id"]] = info
            if start + INFO_BATCH < len(paths):
                self._sleeper(random.uniform(*CHUNK_SLEEP))
        return out

    def iter_media(self, media_type: str | None = None) -> Iterator[Resource]:
        """Все элементы безлимита с датами/превью/ссылками, по image/video."""
        for _cid, resource in self.iter_cluster_media(None, None, media_type):
            yield resource

    def iter_cluster_media(
        self, slice_id: str | None, clusters: list[dict] | None,
        media_type: str | None = None,
    ) -> Iterator[tuple[str, Resource]]:
        """Элементы указанных кластеров: (id кластера, ресурс). Без списка —
        вся лента (первичный обход); чанками по CLUSTER_CHUNK с паузами."""
        if slice_id is None or clusters is None:
            slice_id, clusters = self.snapshot()
        for start in range(0, len(clusters), CLUSTER_CHUNK):
            chunk = clusters[start:start + CLUSTER_CHUNK]
            for cid, paths in self._cluster_paths(slice_id, chunk).items():
                infos = self.bulk_info(paths) if paths else {}
                for path in paths:
                    resource = map_resource_info(infos.get(path) or {"id": path})
                    if resource is not None and (media_type is None
                                                 or resource.media_type == media_type):
                        yield cid, resource
            if start + CLUSTER_CHUNK < len(clusters):
                self._sleeper(random.uniform(*CHUNK_SLEEP))

    def item_url(self, path: str, inline: bool = True) -> str | None:
        """Прямая ссылка на элемент (mpfs/url): работает и для видео, у
        которых в sizes нет ORIGINAL. inline=True —Disposition для стрима."""
        data = self._call("mpfs/url", {"path": path, "inline": inline})
        url = data.get("file") if isinstance(data, dict) else None
        if isinstance(url, str) and url.startswith("//"):
            return "https:" + url
        return url or None

    def download_links(self, paths: list[str]) -> dict[str, str]:
        """Свежие ссылки на оригиналы (ссылки короткоживущие — брать перед
        скачиванием). Формат: {путь "/photounlim/…": url}. Фото получают
        ORIGINAL из bulk-инфо; видео (без ORIGINAL) — поштучно mpfs/url."""
        links: dict[str, str] = {}
        for path, info in self.bulk_info(paths).items():
            url = _sizes_by_name(info.get("meta") or {}).get("ORIGINAL")
            if url:
                links[path] = url
        for path in paths:                    # видео без ORIGINAL — mpfs/url
            if path in links:
                continue
            try:
                url = self.item_url(path)
            except UnlimError:
                url = None
            if url:
                links[path] = url
        return links

    def delete_resources(self, paths: list[str]) -> list[str]:
        """Удалить элементы безлимита в корзину (mpfs/bulk-async-delete).

        Операции идут в обёртке {"operations": [{src: …}]} пачками ≤40 —
        простой список сервер отвергает (500 UNKNOWN_ERROR, проверено
        живьём). Ответ — список операций [{oid, type: "trash", …}] без echo
        путей: сколько строк пришло, столько путей и считаем принятыми.
        Удалённое восстановимо из корзины (mpfs/bulk-async-restore).
        """
        accepted: list[str] = []
        for start in range(0, len(paths), DELETE_BATCH):
            batch = paths[start:start + DELETE_BATCH]
            data = self._call("mpfs/bulk-async-delete",
                              {"operations": [{"src": p} for p in batch]})
            rows = data if isinstance(data, list) else (
                (data.get("data") or data.get("items") or [])
                if isinstance(data, dict) else [])
            if rows:
                accepted.extend(batch[:len(rows)])
            else:
                accepted.extend(batch)   # 200 без ошибок — пачка принята
            if start + DELETE_BATCH < len(paths):
                self._sleeper(0.3)
        return accepted

    def diagnostic_dump(self) -> dict:
        """Сырые ответы цепочки — для уточнения полей при изменениях API."""
        init = self._call("intapi/photo-init-snapshot")
        slice_id = init.get("photoslice_id") if isinstance(init, dict) else None
        snap = self._call("intapi/photo-get-snapshot",
                          {"id": slice_id, "locale": "ru"}) if slice_id else {}
        clusters = [c for c in (snap.get("items") or [])
                    if (c.get("albums") or {}).get("photounlim", 0) > 0]
        first: dict = {}
        bulk: dict = {}
        if clusters:
            probe = clusters[0]
            first = self._call("intapi/photo-get-clusters-with-resources", {
                "photosliceId": slice_id,
                "clusters": {
                    probe["id"]: {"range": [0, max(int(probe.get("size") or 1) - 1, 0)]},
                },
                "hideScreenshots": False,
            })
            paths = [i.get("id") for c in (first.get("clusters") or {}).get("fetched", [])
                     for i in c.get("items", [])
                     if isinstance(i.get("id"), str)]
            if paths:
                bulk = self.bulk_info(paths[:5])
        return {"init": init, "snapshot": snap,
                "first_clusters": first, "bulk_info": bulk}

    def fetch(self, url: str, stream: bool = False) -> requests.Response:
        """GET произвольной ссылки кукисессией (превью/скачивание)."""
        with self._lock:
            return self._session.get(url, timeout=self._timeout, stream=stream)


def _json_body(resp: requests.Response) -> dict:
    try:
        body = resp.json()
    except ValueError:
        raise UnlimError(f"non-JSON response from {resp.url}") from None
    return body if isinstance(body, dict) else {"data": body}
