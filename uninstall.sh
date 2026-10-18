#!/usr/bin/env bash
# Удаление приложения (код и venv). Данные пользователя остаются:
#   ~/.config/yandex-disk-linux/   настройки и аккаунты
#   ~/.cache/yandex-disk-linux/    кэш превью и открываемых файлов
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rm -rf "$DIR/venv"
rm -f "$HOME/.local/share/applications/yandex-disk-linux.desktop" \
      "$HOME/.config/autostart/yandex-disk-linux.desktop"
echo "Удалены venv и ярлыки. Для полного удаления также выполните:"
echo "  rm -rf ~/.config/yandex-disk-linux ~/.cache/yandex-disk-linux"
