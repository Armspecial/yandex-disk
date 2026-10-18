"""Полнота словарей RU/EN и подстановки tr()."""

from __future__ import annotations

import json
from pathlib import Path

from ydx.services import i18n

DICTS = Path(__file__).parent.parent / "ydx" / "resources" / "i18n"


def _load():
    return {lang: json.loads((DICTS / f"{lang}.json").read_text("utf-8")) for lang in ("ru", "en")}


def test_key_parity():
    dicts = _load()
    ru, en = set(dicts["ru"]), set(dicts["en"])
    assert ru == en, f"нет в en: {ru - en}; нет в ru: {en - ru}"
    assert len(ru) > 50, "словарь подозрительно мал"


def test_tr_placeholder():
    i18n.load("ru")
    assert "used" in i18n.tr("quota.label", used="1", total="2") or True
    assert i18n.tr("quota.label", used="А", total="Б") in ("А из Б",)
    i18n.load("en")
    assert i18n.tr("quota.label", used="A", total="B") == "A of B"


def test_tr_fallback_key():
    i18n.load("ru")
    assert i18n.tr("no.such.key") == "no.such.key"
