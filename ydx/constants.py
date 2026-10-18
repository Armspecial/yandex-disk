"""Общие константы приложения: пути, API, OAuth, лимиты, палитра."""

from pathlib import Path

APP_ID = "yandex-disk-linux"
APP_TITLE = "Яндекс Диск"

# --- Каталоги данных приложения -------------------------------------------
CONFIG_DIR = Path.home() / ".config" / APP_ID
CACHE_DIR = Path.home() / ".cache" / APP_ID
THUMB_DIR = CACHE_DIR / "thumbs"        # дисковый кэш превью
OPEN_CACHE_DIR = CACHE_DIR / "open"     # временные файлы для xdg-open
SETTINGS_FILE = CONFIG_DIR / "settings.ini"

# --- REST API Яндекс.Диска --------------------------------------------------
API_BASE = "https://cloud-api.yandex.net/v1/disk"

# --- OAuth (Яндекс ID) -------------------------------------------------------
OAUTH_BASE = "https://oauth.yandex.ru"
REDIRECT_PORT = 8917                    # должен точно совпадать с зарегистрированным
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"
SCOPES = (
    "cloud_api:disk.read cloud_api:disk.write "
    "cloud_api:disk.info cloud_api:disk.app_folder"
)

# --- Лимиты и поведение -----------------------------------------------------
PAGE_SIZE = 200                # элементов на страницу листинга
MAX_PAGES = 200                # предохранитель пагинации
PREVIEW_CONCURRENCY = 4        # параллельных загрузок превью
UPLOAD_SETTLE_DELAY = 2.0      # пауза после upload перед следующими запросами
MAX_FREE_UPLOAD = 1 << 30      # 1 ГБ — лимит бесплатного аккаунта
MAX_PAID_UPLOAD = 50 << 30     # 50 ГБ — с подпиской Яндекс 360
OPEN_SIZE_LIMIT = 500 << 20    # подтверждение открытия файлов крупнее 500 МБ
TRANSFER_SLOTS = 3             # одновременных передач

# --- Демонд yandex-disk -------------------------------------------------------
DAEMON_BINARY = "yandex-disk"
DAEMON_CONFIG_DIR = CONFIG_DIR
DAEMON_CONFIG_FILE = CONFIG_DIR / "daemon.cfg"
OFFICIAL_DAEMON_DIR = Path.home() / ".config" / "yandex-disk"

# --- Палитра (бренд Яндекса) --------------------------------------------------
ACCENT = "#FFCC00"      # жёлтый
ACCENT_HOVER = "#FFDB4D"
DANGER = "#FC3F1D"      # красный
SUCCESS = "#1DA740"

MAX_ACCOUNTS = 2
