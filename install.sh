#!/usr/bin/env bash
# Установка «Яндекс.Диск для Linux» (PySide6-клиент).
# Работает из каталога с пробелами в пути: все пути в кавычках.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- 1. Системные пакеты (venv + рантайм-библиотеки Qt) --------------------
# Имена библиотек различаются между выпусками Ubuntu (libglib2.0-0 / ...t64),
# поэтому проверяем оба варианта.
need=()
check_pkg() {  # $1=пакет, остальные — альтернативные имена
    local names=("$@") found=0 name
    for name in "${names[@]}"; do
        if dpkg-query -W -f='${Status}' "$name" 2>/dev/null | grep -q 'install ok installed'; then
            found=1; break
        fi
    done
    [ "$found" = 1 ] || need+=("${names[0]}")
}
check_pkg python3
check_pkg python3-venv
check_pkg libegl1
check_pkg libgl1
check_pkg libxkbcommon0
check_pkg libfontconfig1
check_pkg libglib2.0-0t64 libglib2.0-0
check_pkg libdbus-1-3

if [ "${#need[@]}" -gt 0 ]; then
    echo "Не хватает системных пакетов: ${need[*]}"
    echo "Выполните:  sudo apt install -y ${need[*]}"
    echo "и запустите install.sh снова."
    exit 1
fi

# --- 2. Виртуальное окружение ----------------------------------------------
# На Ubuntu pip в системе отсутствует (PEP 668), поэтому при необходимости
# бутстрапим pip прямо в venv через get-pip.py — sudo не требуется.
if [ ! -x "$DIR/venv/bin/python3" ]; then
    echo "Создаю venv в $DIR/venv"
    if ! python3 -m venv "$DIR/venv" 2>/dev/null; then
        python3 -m venv --without-pip "$DIR/venv"
        curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
        "$DIR/venv/bin/python3" /tmp/get-pip.py --quiet
    fi
fi

# --- 3. Зависимости Python ---------------------------------------------------
echo "Устанавливаю зависимости Python…"
"$DIR/venv/bin/python3" -m pip install --quiet -r "$DIR/requirements.txt"

echo
echo "Готово. Запуск приложения:  \"$DIR/run.sh\""
echo "Ярлык в меню:               скопируйте yandex-disk-linux.desktop в ~/.local/share/applications/"
