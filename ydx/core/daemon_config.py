"""Чтение/запись config.cfg демона с сохранением порядка и чужих ключей.

Формат — плоский `key=value`, комментарии с '#'. Мы обновляем только свои
ключи (dir, auth, exclude-dirs, read-only, overwrite, proxy), остальное
переносим байт-в-байт.
"""

from __future__ import annotations

from pathlib import Path


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read_config(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = _unquote(value)
    return result


def write_config(path: Path, updates: dict[str, str]) -> None:
    """Обновить свои ключи, сохранив чужие строки и комментарии."""
    lines: list[str] = []
    if path.exists():
        lines = path.read_text("utf-8", errors="replace").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n", "utf-8")
    tmp.replace(path)


def exclude_dirs_str(dirs: list[str]) -> str:
    return ",".join(d.strip().strip("/") for d in dirs if d.strip())


def parse_exclude_dirs(value: str) -> list[str]:
    return [p for p in (x.strip() for x in value.split(",")) if p]
