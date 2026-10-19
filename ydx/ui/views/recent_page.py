"""Страница «Недавние»: последние загруженные файлы + «показать в папке»."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ydx.core.models import Resource
from ydx.services import i18n, openers
from ydx.services.accounts import AccountManager
from ydx.services.thumbs import ThumbService
from ydx.services.workers import run_in_thread
from ydx.ui.dialogs.prompts import PropertiesDialog
from ydx.ui.views.files_view import FilesView
from ydx.ui.views.resource_list import ResourceListModel


class RecentPage(QWidget):
    show_in_folder_requested = Signal(str)   # путь родительской папки

    def __init__(self, accounts: AccountManager, thumbs: ThumbService, parent=None) -> None:
        super().__init__(parent)
        self._accounts = accounts
        self._thumbs = thumbs

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        header = QWidget()
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 12, 16, 8)
        self.title = QLabel()
        self.title.setObjectName("dialogTitle")
        hlay.addWidget(self.title)
        hlay.addStretch(1)
        col.addWidget(header)

        self.stack = QStackedWidget()
        self.model = ResourceListModel(self)
        self.view = FilesView(self.model, thumbs)
        self.view.set_mode("list")
        self.stack.addWidget(self.view)
        self.loading_label = QLabel()
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.loading_label)
        self.empty_label = QLabel()
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.empty_label)
        col.addWidget(self.stack, 1)

        self.view.open_requested.connect(self._open)
        self.view.context_menu_requested.connect(self._context_menu)
        self.model.modelReset.connect(self._update_states)
        self.model.rowsInserted.connect(lambda *_: self._update_states())

        i18n.register(self.retranslate)
        self.retranslate()

    def on_shown(self) -> None:
        resources = self._accounts.resources
        if resources is None:
            return

        def job():
            return resources.last_uploaded(limit=50)

        run_in_thread(job, self.model.set_items, lambda _e: None)
        self._update_states()

    def _update_states(self) -> None:
        if not self.model.rowCount():
            self.stack.setCurrentWidget(self.empty_label)
        else:
            self.stack.setCurrentWidget(self.view)

    def _open(self, resource: Resource) -> None:
        if resource.is_dir:
            return
        resources = self._accounts.resources
        if resources is None:
            return
        from ydx.ui.views.viewer import PhotoViewer, VideoViewer, viewer_kind

        kind = viewer_kind(resource)
        if kind == "photo":
            PhotoViewer([resource], 0, self._thumbs, self).exec()
        elif kind == "video":
            VideoViewer(resource, resources, self).exec()
        else:
            openers.open_resource(self, resources, resource)

    def _show_in_folder(self, resource: Resource) -> None:
        parent = resource.path.rsplit("/", 1)[0] or "disk:/"
        self.show_in_folder_requested.emit(parent)

    def _context_menu(self, global_pos, resources: list[Resource]) -> None:
        if not resources:
            return
        single = resources[0] if len(resources) == 1 else None
        menu = QMenu(self)
        if single is not None:
            act_open = QAction(i18n.tr("ctx.open"), menu)
            act_open.triggered.connect(lambda: self._open(single))
            menu.addAction(act_open)
            act_folder = QAction(i18n.tr("recent.show_in_folder"), menu)
            act_folder.triggered.connect(lambda: self._show_in_folder(single))
            menu.addAction(act_folder)
            menu.addSeparator()
            act_props = QAction(i18n.tr("ctx.properties"), menu)
            act_props.triggered.connect(
                lambda: PropertiesDialog(single, self._thumbs, self).exec()
            )
            menu.addAction(act_props)
        menu.exec(global_pos)

    def retranslate(self) -> None:
        self.title.setText(i18n.tr("recent.title"))
        self.loading_label.setText(i18n.tr("files.loading"))
        self.empty_label.setText(i18n.tr("recent.empty"))
