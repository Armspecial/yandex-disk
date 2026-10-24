#!/usr/bin/env bash
# Сборка .deb-пакета: standalone-бинарник (PyInstaller, без WebEngine/Pdf)
# + /opt/yandex-disk-linux + /usr/bin/yandex-disk-linux + ярлык + иконка.
#
# Результат: dist/yandex-disk-linux_<версия>-1_amd64.deb
# Установка:  sudo apt install ./dist/yandex-disk-linux_*_amd64.deb
#
# Путь с пробелами — все переменные в кавычках.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$DIR"

pin() {  # pin <пакет> — версия из requirements.txt
    grep -oP "(?<=$1==)[0-9.]+" requirements.txt
}

VERSION="$(./venv/bin/python3 -c 'import sys; sys.path.insert(0, "."); from ydx import __version__; print(__version__)')"
PYSIDE="$(pin PySide6)"
BUILD_VENV="packaging/.build-venv"

# --- 1. Сборочный venv: Essentials+Addons (Qt Widgets и Multimedia), без WebEngine --
if [ ! -x "$BUILD_VENV/bin/python3" ]; then
    echo "Создаю сборочный venv ($BUILD_VENV)…"
    # как в install.sh: без python3-venv бутстрапим pip через get-pip.py
    rm -rf "$BUILD_VENV"
    if ! python3 -m venv "$BUILD_VENV" 2>/dev/null; then
        python3 -m venv --without-pip "$BUILD_VENV"
        curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
        "$BUILD_VENV/bin/python3" /tmp/get-pip.py --quiet
    fi
fi
"$BUILD_VENV/bin/python3" -m pip install --quiet \
    "PySide6-Essentials==$PYSIDE" "PySide6-Addons==$PYSIDE" \
    "requests==$(pin requests)" "keyring==$(pin keyring)" \
    pyinstaller

# --- 2. PyInstaller -----------------------------------------------------------
# add-data кладёт ydx/resources рядом с модулем — i18n и иконки грузятся
# по __file__ и в заморозке находят их. keyring импортируется динамически,
# бэкенд SecretService указываем явно (не сработает — будет файл 0600).
"$BUILD_VENV/bin/pyinstaller" --noconfirm --clean --noconsole \
    --name yandex-disk-linux \
    --add-data "ydx/resources:ydx/resources" \
    --hidden-import keyring.backends.SecretService \
    --hidden-import secretstorage \
    ydx/__main__.py

# --- 3. Дерево пакета ---------------------------------------------------------
APP="dist/yandex-disk-linux"
PKG="dist/pkg"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" \
         "$PKG/opt/yandex-disk-linux" \
         "$PKG/usr/bin" \
         "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/scalable/apps"

cp -a "$APP/." "$PKG/opt/yandex-disk-linux/"
printf '#!/bin/sh\nexec /opt/yandex-disk-linux/yandex-disk-linux "$@"\n' \
    > "$PKG/usr/bin/yandex-disk-linux"
chmod 755 "$PKG/usr/bin/yandex-disk-linux"
cp packaging/yandex-disk-linux.desktop "$PKG/usr/share/applications/"
cp ydx/resources/icons/app.svg \
    "$PKG/usr/share/icons/hicolor/scalable/apps/yandex-disk-linux.svg"

SIZE_KB="$(du -sk --apparent-size "$PKG/opt" | cut -f1)"

cat > "$PKG/DEBIAN/control" <<EOF
Package: yandex-disk-linux
Version: $VERSION-1
Section: net
Priority: optional
Architecture: amd64
Depends: libegl1, libgl1, libxkbcommon0, libfontconfig1, libglib2.0-0t64 | libglib2.0-0, libdbus-1-3, libxcb-cursor0, libavcodec60 | libavcodec-extra60 | libavcodec59, libavformat60 | libavformat59, libavutil58 | libavutil56, libswresample4 | libswresample3, libswscale7 | libswscale6
Installed-Size: $SIZE_KB
Maintainer: armspecial <205297729+Armspecial@users.noreply.github.com>
Description: Неофициальный клиент Яндекс.Диска для Linux
 Файловый браузер, фотогалерея со встроенными просмотрщиками фото и видео,
 публичные ссылки, корзина, очередь передач, трей и управление официальным
 демоном yandex-disk (опционально). Python + PySide6 (Qt 6). Не связан
 с Яндексом; для входа нужен свой OAuth-ключ (см. README).
EOF

cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
update-desktop-database -q /usr/share/applications 2>/dev/null || true
gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
exit 0
EOF
chmod 755 "$PKG/DEBIAN/postinst"

# --- 4. Сборка -----------------------------------------------------------------
DEB="dist/yandex-disk-linux_${VERSION}-1_amd64.deb"
dpkg-deb --build --root-owner-group "$PKG" "$DEB"
rm -rf "$PKG"

echo
echo "Готово: $DEB ($(du -h "$DEB" | cut -f1))"
echo "Установка: sudo apt install \"$DEB\""
