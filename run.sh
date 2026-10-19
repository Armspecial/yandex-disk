#!/usr/bin/env bash
# Запуск «Яндекс.Диск для Linux». Путь с пробелами — всегда в кавычках.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# vendor/lib: локальные системные библиотеки (libxcb-cursor0 нужен Qt>=6.5,
# ставится через install.sh; здесь — запасной вариант без sudo)
export LD_LIBRARY_PATH="$DIR/vendor/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$DIR/venv/bin/python3" -m ydx "$@"
