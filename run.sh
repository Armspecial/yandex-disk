#!/usr/bin/env bash
# Запуск «Яндекс.Диск для Linux». Путь с пробелами — всегда в кавычках.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/venv/bin/python3" -m ydx "$@"
