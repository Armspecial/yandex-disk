"""Локализация: JSON-словари RU/EN + живое переключение.

tr("key", **kwargs) — перевод с подстановкой {placeholders}.
Виджеты регистрируют retranslate-колбэки и обновляют тексты при смене языка.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

_lang = "ru"
_dicts: dict[str, dict[str, str]] = {}
_callbacks: list[Callable[[], None]] = []

I18N_DIR = Path(__file__).parent.parent / "resources" / "i18n"
log = logging.getLogger(__name__)


def load(lang: str) -> None:
    global _lang, _dicts
    _lang = lang if lang in ("ru", "en") else "ru"
    _dicts = {}
    for code in ("ru", "en"):
        path = I18N_DIR / f"{code}.json"
        try:
            _dicts[code] = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            log.error("не удалось прочитать словарь %s", path)
            _dicts[code] = {}


def tr(key: str, **kwargs: object) -> str:
    text = _dicts.get(_lang, {}).get(key) or _dicts.get("en", {}).get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError):
            return text
    return text


def register(callback: Callable[[], None]) -> None:
    if callback not in _callbacks:
        _callbacks.append(callback)


def unregister(callback: Callable[[], None]) -> None:
    """Отменить регистрацию (диалоги при закрытии — иначе утечка колбэков)."""
    try:
        _callbacks.remove(callback)
    except ValueError:
        pass


def set_language(lang: str) -> None:
    global _lang
    lang = lang if lang in _dicts or lang in ("ru", "en") else "ru"
    load(lang)
    for cb in list(_callbacks):
        try:
            cb()
        except Exception:  # noqa: BLE001 — падение одного виджета не должно ломать смену языка
            log.exception("retranslate callback failed")


def current_language() -> str:
    return _lang
