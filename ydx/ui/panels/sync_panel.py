"""Диалог «Синхронизация»: статус демона, старт/стоп/пауза,
выборочная синхронизация (exclude-dirs), read-only, папка."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ydx.core.daemon_config import exclude_dirs_str, parse_exclude_dirs
from ydx.core.models import DaemonStatus
from ydx.services import i18n
from ydx.services.daemon import DaemonController
from ydx.services.workers import run_in_thread
from ydx.ui.errors_ui import confirm
from ydx.services.fmt import human_size


class SyncDialog(QDialog):
    def __init__(self, daemon: DaemonController, resources, parent=None) -> None:
        super().__init__(parent)
        self._daemon = daemon
        self._resources = resources
        self._status = DaemonStatus()
        self._exclude: set[str] = set()
        self._readonly = False
        self._dirty = False

        self.setModal(True)
        self.setMinimumSize(560, 620)
        self.setWindowTitle(i18n.tr("sync.title"))

        col = QVBoxLayout(self)
        col.setContentsMargins(22, 20, 22, 18)
        col.setSpacing(12)

        self.state_label = QLabel()
        self.state_label.setObjectName("dialogTitle")
        col.addWidget(self.state_label)
        self.detail_label = QLabel()
        self.detail_label.setObjectName("dialogHint")
        self.detail_label.setWordWrap(True)
        col.addWidget(self.detail_label)
        self.paywall_banner = QLabel()
        self.paywall_banner.setObjectName("errorBanner")
        self.paywall_banner.setWordWrap(True)
        self.paywall_banner.hide()
        col.addWidget(self.paywall_banner)

        buttons = QHBoxLayout()
        self.start_btn = QPushButton()
        self.start_btn.setProperty("class", "primary")
        self.start_btn.clicked.connect(self._start)
        self.stop_btn = QPushButton()
        self.stop_btn.clicked.connect(self._stop)
        self.pause_btn = QPushButton()
        self.pause_btn.clicked.connect(lambda: self._pause(2))
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.pause_btn)
        buttons.addStretch(1)
        col.addLayout(buttons)

        # папка
        folder_row = QHBoxLayout()
        self.folder_label = QLabel()
        self.folder_label.setObjectName("dialogHint")
        folder_row.addWidget(self.folder_label, 1)
        self.folder_btn = QPushButton()
        self.folder_btn.clicked.connect(self._change_folder)
        folder_row.addWidget(self.folder_btn)
        col.addLayout(folder_row)

        # read-only
        self.readonly_check = QCheckBox()
        self.readonly_check.toggled.connect(self._mark_dirty)
        col.addWidget(self.readonly_check)

        # выборочная синхронизация
        self.selective_title = QLabel()
        self.selective_title.setObjectName("dialogHint")
        col.addWidget(self.selective_title)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(lambda *_: self._mark_dirty())
        col.addWidget(self.tree, 1)

        close_row = QHBoxLayout()
        self.apply_btn = QPushButton()
        self.apply_btn.setProperty("class", "primary")
        self.apply_btn.clicked.connect(self._apply)
        close_btn = QPushButton(i18n.tr("common.close"))
        close_btn.clicked.connect(self.accept)
        close_row.addStretch(1)
        close_row.addWidget(self.apply_btn)
        close_row.addWidget(close_btn)
        col.addLayout(close_row)

        self._load_config()
        self._load_folders()
        self.retranslate()

        daemon.status_updated.connect(self._on_status)
        self._daemon.refresh_status()

    # --- данные -------------------------------------------------------------------

    def _load_config(self) -> None:
        cfg = self._daemon.config()
        self.folder_label.setText(
            (i18n.tr("sync.folder") + ": " + cfg.get("dir", "—")) if cfg.get("dir")
            else i18n.tr("sync.setup_hint")
        )
        self._exclude = set(parse_exclude_dirs(cfg.get("exclude-dirs", "")))
        self._readonly = cfg.get("read-only", "false").lower() == "true"
        self.readonly_check.blockSignals(True)
        self.readonly_check.setChecked(self._readonly)
        self.readonly_check.blockSignals(False)
        self._dirty = False
        self._update_apply()

    def _load_folders(self) -> None:
        if self._resources is None:
            return

        def job():
            items, _ = self._resources.list_page("disk:/", limit=500)
            return [r.name for r in items if r.is_dir]

        run_in_thread(job, self._fill_tree, lambda _e: None)

    def _fill_tree(self, dir_names: list[str]) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        for name in dir_names:
            item = QTreeWidgetItem([name])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                0,
                Qt.CheckState.Checked if name in self._exclude else Qt.CheckState.Unchecked,
            )
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)

    # --- управление демоном -----------------------------------------------------------

    def _start(self) -> None:
        if not self._daemon.config().get("dir") and not self._daemon.ensure_config():
            self.detail_label.setText(i18n.tr("login.welcome") + " — " + i18n.tr("sync.setup_hint"))
            return
        self._daemon.ensure_config()
        self._daemon.start()

    def _stop(self) -> None:
        self._daemon.cancel_pause()
        self._daemon.stop()

    def _pause(self, hours: float) -> None:
        self._daemon.pause_for(hours)

    def _on_status(self, status: DaemonStatus) -> None:
        self._status = status
        state_key = f"sync.state.{status.state}"
        text = i18n.tr(state_key)
        if state_key == text:   # нет перевода
            text = status.state
        self.state_label.setText(text)
        details = []
        if status.progress:
            details.append(status.progress)
        if status.used:
            details.append(i18n.tr("quota.label",
                                   used=human_size(status.used),
                                   total=human_size(status.total or 0)))
        self.detail_label.setText(" · ".join(details))
        self.paywall_banner.setVisible(status.state == "paywall")
        running = status.state in ("idle", "busy", "paused", "index")
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running or status.state == "paywall")

    # --- применение настроек ---------------------------------------------------------------

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_apply()

    def _update_apply(self) -> None:
        self.apply_btn.setEnabled(self._dirty)

    def _change_folder(self) -> None:
        current = self._daemon.config().get("dir", str(Path.home()))
        folder = QFileDialog.getExistingDirectory(self, i18n.tr("sync.folder"), current)
        if folder:
            self.folder_label.setText(i18n.tr("sync.folder") + ": " + folder)
            self._new_dir = folder
            self._mark_dirty()

    def _apply(self) -> None:
        excluded = []
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) == Qt.CheckState.Checked:
                excluded.append(item.text(0))
        updates: dict[str, str] = {
            "exclude-dirs": exclude_dirs_str(excluded),
            "read-only": "true" if self.readonly_check.isChecked() else "false",
        }
        new_dir = getattr(self, "_new_dir", None)
        if new_dir:
            updates["dir"] = new_dir
        self._daemon.apply_config(updates)

        was_running = self._status.state in ("idle", "busy", "paused", "index")
        self._load_config()
        self._update_apply()
        if was_running and confirm(self, i18n.tr("sync.restart_needed")):
            self._daemon.stop()
            from PySide6.QtCore import QTimer

            QTimer.singleShot(1500, self._daemon.start)

    def retranslate(self) -> None:
        self.start_btn.setText(i18n.tr("sync.start"))
        self.stop_btn.setText(i18n.tr("sync.stop"))
        self.pause_btn.setText(i18n.tr("sync.pause"))
        self.folder_btn.setText(i18n.tr("sync.folder"))
        self.readonly_check.setText(i18n.tr("sync.readonly"))
        self.selective_title.setText(
            i18n.tr("sync.selective") + " — " + i18n.tr("sync.selective_hint")
        )
        self.paywall_banner.setText(i18n.tr("sync.paywall_hint"))
        self.apply_btn.setText(i18n.tr("common.ok"))
