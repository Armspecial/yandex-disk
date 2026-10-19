"""Парсер вывода yandex-disk status + конфиг config.cfg."""

from __future__ import annotations

from pathlib import Path

from ydx.core.daemon_config import (
    exclude_dirs_str,
    parse_exclude_dirs,
    read_config,
    write_config,
)
from ydx.core.daemon_status import (
    looks_like_paywall,
    parse_size,
    parse_status,
    sync_percent,
    sync_speed,
)


def test_sync_percent_and_speed():
    assert sync_percent("45% (10.5 MB/s)") == 45.0
    assert sync_percent("3,5% (120 KB/s)") == 3.5
    assert sync_percent("без процентов") is None
    assert sync_percent("") is None
    assert sync_speed("45% (10.5 MB/s)") == "10.5 MB/s"
    assert sync_speed("45%") == ""


def test_parse_size_units():
    assert parse_size("1024") == 1024
    assert parse_size("1.5 KB") == 1536
    assert parse_size("2 MB") == 2 * 1024**2
    assert parse_size("1,5 GB") == int(1.5 * 1024**3)
    assert parse_size("мусор") == 0


def test_status_empty_is_stopped():
    assert parse_status("").state == "stopped"
    assert parse_status("\n \n").state == "stopped"


def test_status_idle_with_totals():
    raw = (
        "Synchronization core status: idle\n"
        "Total: 26843545600\n"
        "Used: 12884901888\n"
        "Available: 13958643712\n"      # реальный формат демона (не Free:)
        "Trash size: 1.5 GB\n"
    )
    s = parse_status(raw)
    assert s.state == "idle"
    assert s.total == 26843545600
    assert s.used == 12884901888
    assert s.free == 13958643712
    assert s.trash == int(1.5 * 1024**3)


def test_status_busy_last_items():
    raw = (
        "Synchronization core status: busy\n"
        "Sync progress: 45% (10.5 MB/s)\n"
        "Last synchronized items:\n"
        "  file: 'отчёт (2).pdf'\n"
        "  directory: 'Фото 2026'\n"   # реальный формат: directory:
    )
    s = parse_status(raw)
    assert s.state == "busy"
    assert "45%" in s.progress
    assert s.last_items == ["отчёт (2).pdf", "Фото 2026"]


def test_status_daemon_not_started_is_stopped():
    # реальные выводы остановленного/ненастроенного демона (бинар 0.1.6.1080)
    assert parse_status("Error: daemon not started").state == "stopped"
    assert parse_status("Error: option 'dir' is missing").state == "stopped"


def test_status_no_internet_and_error_paywall():
    s = parse_status("Synchronization core status: no internet access")
    assert s.state == "no-internet"
    err = parse_status("Error: sync requires Yandex 360 subscription")
    assert err.state == "error"
    assert looks_like_paywall(err)
    plain = parse_status("Error: disk path not found")
    assert not looks_like_paywall(plain)


def test_paywall_no_false_positive_on_paths():
    s = parse_status("Error: cannot read /media/disk360/photo")
    assert s.state == "error"
    assert not looks_like_paywall(s)   # «360» в пути — не пейволл


def test_config_roundtrip_preserves_foreign(tmp_path: Path):
    cfg = tmp_path / "config.cfg"
    cfg.write_text(
        "# мой комментарий\n"
        "dir=\"/old/path\"\n"
        "unknown-key=42\n"
        "read-only=false\n",
        "utf-8",
    )
    write_config(cfg, {"dir": "/new/path", "exclude-dirs": "a,b"})
    text = cfg.read_text("utf-8")
    assert "# мой комментарий" in text
    assert "unknown-key=42" in text
    data = read_config(cfg)
    assert data["dir"] == "/new/path"
    assert data["exclude-dirs"] == "a,b"
    assert data["unknown-key"] == "42"
    assert data["read-only"] == "false"


def test_exclude_dirs_helpers():
    assert exclude_dirs_str(["a", " b/ ", "", "/c/"]) == "a,b,c"
    assert parse_exclude_dirs("a, b ,,c") == ["a", "b", "c"]
