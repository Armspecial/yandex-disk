"""Диалог настроек: тема и язык (применяются сразу), автозапуск, аккаунт, о программе."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ydx import __version__
from ydx.constants import APP_ID
from ydx.services import i18n
from ydx.services.settings import AppSettings
from ydx.ui.theme import apply_theme

AUTOSTART_FILE = Path.home() / ".config" / "autostart" / f"{APP_ID}.desktop"
AUTOSTART_DESKTOP = (
    "[Desktop Entry]\n"
    "Type=Application\n"
    'Exec="{exe}"\n'
    "Name=Yandex Disk (unofficial)\n"
    "X-GNOME-Autostart-enabled=true\n"
)


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, app, parent=None, daemon=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._app = app
        self._daemon = daemon

        self.setModal(True)
        self.setMinimumSize(480, 520)
        self.setWindowTitle(i18n.tr("settings.title"))

        col = QVBoxLayout(self)
        col.setContentsMargins(22, 20, 22, 18)
        col.setSpacing(14)

        general = QGroupBox(i18n.tr("settings.general"))
        self._general_box = general
        form = QFormLayout(general)
        form.setSpacing(10)

        self.theme_combo = QComboBox()
        for value, key in (("system", "settings.theme.system"),
                           ("light", "settings.theme.light"),
                           ("dark", "settings.theme.dark")):
            self.theme_combo.addItem(i18n.tr(key), value)
        self.theme_combo.setCurrentIndex(
            [self.theme_combo.itemData(i) for i in range(3)].index(settings.theme)
        )
        self.theme_combo.currentIndexChanged.connect(self._theme_changed)
        self._theme_row = QLabel(i18n.tr("settings.theme"))
        form.addRow(self._theme_row, self.theme_combo)

        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Русский", "ru")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.setCurrentIndex(0 if settings.language == "ru" else 1)
        self.lang_combo.currentIndexChanged.connect(self._lang_changed)
        self._lang_row = QLabel(i18n.tr("settings.language"))
        form.addRow(self._lang_row, self.lang_combo)

        from PySide6.QtWidgets import QCheckBox

        self.autostart_check = QCheckBox(i18n.tr("settings.autostart"))
        self.autostart_check.setChecked(AUTOSTART_FILE.exists())
        self.autostart_check.toggled.connect(self._autostart_toggled)
        form.addRow("", self.autostart_check)

        col.addWidget(general)

        # синхронизация: папку можно поменять в любой момент
        if self._daemon is not None and self._daemon.installed:
            sync_box = QGroupBox(i18n.tr("sync.title"))
            self._sync_box = sync_box
            srow = QHBoxLayout(sync_box)
            self.sync_folder_label = QLabel()
            self.sync_folder_label.setObjectName("dialogHint")
            change_btn = QPushButton(i18n.tr("sync.change_folder"))
            change_btn.clicked.connect(self._change_sync_folder)
            self.sync_now_btn = QPushButton(i18n.tr("sync.sync_now"))
            self.sync_now_btn.setProperty("class", "primary")
            self.sync_now_btn.clicked.connect(self._daemon.sync_now)
            srow.addWidget(self.sync_folder_label, 1)
            srow.addWidget(change_btn)
            srow.addWidget(self.sync_now_btn)
            col.addWidget(sync_box)
            self._update_sync_folder_label()

        # хранилище токенов
        storage = QGroupBox(i18n.tr("settings.account"))
        self._account_box = storage
        srow = QHBoxLayout(storage)
        self.storage_label = QLabel()
        self.storage_label.setObjectName("dialogHint")
        self.storage_label.setWordWrap(True)
        srow.addWidget(self.storage_label, 1)
        col.addWidget(storage)

        col.addStretch(1)

        about = QLabel(i18n.tr("settings.about_text", version=__version__))
        about.setObjectName("dialogHint")
        about.setWordWrap(True)
        col.addWidget(about)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close = QPushButton(i18n.tr("common.close"))
        close.setProperty("class", "primary")
        close.clicked.connect(self.accept)
        btn_row.addWidget(close)
        col.addLayout(btn_row)

        i18n.register(self.retranslate)
        self.retranslate()

    # --- обработчики ---------------------------------------------------------------

    def _theme_changed(self, index: int) -> None:
        value = self.theme_combo.itemData(index)
        self._settings.set("theme", value)
        apply_theme(self._app, value)

    def _lang_changed(self, index: int) -> None:
        value = self.lang_combo.itemData(index)
        self._settings.set("language", value)
        i18n.set_language(value)
        apply_theme(self._app, self._settings.theme)   # QSS после смены языка перечитать

    # --- синхронизация ------------------------------------------------------------

    def _update_sync_folder_label(self) -> None:
        from ydx.services.sync_state import LocalSyncIndex

        directory = LocalSyncIndex.sync_dir()
        self.sync_folder_label.setText(
            i18n.tr("sync.folder") + ": " + directory if directory
            else i18n.tr("sync.setup_hint")
        )

    def _change_sync_folder(self) -> None:
        from ydx.core.daemon_config import read_config
        from ydx.constants import DAEMON_CONFIG_FILE
        from ydx.ui.errors_ui import confirm

        current = read_config(DAEMON_CONFIG_FILE).get("dir", "")
        folder = QFileDialog.getExistingDirectory(
            self, i18n.tr("sync.choose_folder"), current or str(Path.home())
        )
        if not folder:
            return
        self._daemon.apply_config({"dir": folder})
        self._update_sync_folder_label()
        # конфиг демона читается при старте — предложить перезапуск
        if confirm(self, i18n.tr("sync.restart_needed")):
            self._daemon.stop()

            from PySide6.QtCore import QTimer

            QTimer.singleShot(1500, self._daemon.start)

    def _autostart_toggled(self, on: bool) -> None:
        AUTOSTART_FILE.parent.mkdir(parents=True, exist_ok=True)
        if on:
            run_sh = Path(__file__).resolve().parents[3] / "run.sh"
            AUTOSTART_FILE.write_text(AUTOSTART_DESKTOP.format(exe=run_sh), "utf-8")
        else:
            AUTOSTART_FILE.unlink(missing_ok=True)

    def set_secure(self, secure: bool) -> None:
        self._secure = secure
        self.storage_label.setText(
            i18n.tr("settings.storage_secure") if secure
            else i18n.tr("settings.storage_insecure")
        )

    def retranslate(self) -> None:
        self.setWindowTitle(i18n.tr("settings.title"))
        self.autostart_check.setText(i18n.tr("settings.autostart"))
        self.set_secure(getattr(self, "_secure", True))
        self._general_box.setTitle(i18n.tr("settings.general"))
        self._account_box.setTitle(i18n.tr("settings.account"))
        if hasattr(self, "_sync_box"):
            self._sync_box.setTitle(i18n.tr("sync.title"))
            self.sync_now_btn.setText(i18n.tr("sync.sync_now"))
            self._update_sync_folder_label()
        self._theme_row.setText(i18n.tr("settings.theme"))
        self._lang_row.setText(i18n.tr("settings.language"))
        for value, key in (("system", "settings.theme.system"),
                           ("light", "settings.theme.light"),
                           ("dark", "settings.theme.dark")):
            idx = [self.theme_combo.itemData(i) for i in range(3)].index(value)
            self.theme_combo.setItemText(idx, i18n.tr(key))

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
        i18n.unregister(self.retranslate)
        super().closeEvent(event)
