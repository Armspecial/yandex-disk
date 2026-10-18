"""TokenStore: файловый fallback (0600), сохранение/чтение/удаление."""

from __future__ import annotations

import json

from ydx.services.token_store import TokenStore


def test_file_fallback_roundtrip(tmp_path, monkeypatch):
    from ydx.constants import CONFIG_DIR
    monkeypatch.setattr("ydx.constants.CONFIG_DIR", tmp_path)
    # форсируем файловый режим: keyring подменяем на падающий
    import builtins

    real_import = builtins.__import__

    def no_keyring(name, *a, **kw):
        if name == "keyring":
            raise ImportError("no keyring in test")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_keyring)
    store = TokenStore()
    assert store.secure is False

    store.save("acc1", {"access_token": "at", "refresh_token": "rt", "expires_at": 1.5})
    assert store.load("acc1") == {"access_token": "at", "refresh_token": "rt", "expires_at": 1.5}
    store.save("acc2", {"access_token": "at2"})
    assert set(json.loads((tmp_path / "tokens.json").read_text())) == {"acc1", "acc2"}
    mode = (tmp_path / "tokens.json").stat().st_mode & 0o777
    assert mode == 0o600
    store.delete("acc1")
    assert store.load("acc1") is None
    assert store.load("acc2") == {"access_token": "at2"}
