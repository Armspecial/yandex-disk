"""Страница «Фото»: плоская галерея всех изображений Диска (media_type=image)."""

from __future__ import annotations

from PySide6.QtCore import Qt
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
from ydx.ui.dialogs.prompts import PropertiesDialog
from ydx.ui.views.files_view import FilesView
from ydx.ui.views.resource_list import ResourceListModel
from ydx.ui.views.viewer import PhotoViewer


class PhotosPage(QWidget):
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
        self.counter = QLabel()
        self.counter.setObjectName("dialogHint")
        hlay.addWidget(self.counter)
        hlay.addStretch(1)
        col.addWidget(header)

        self.stack = QStackedWidget()
        self.model = ResourceListModel(self)
        self.view = FilesView(self.model, thumbs)
        self.view.set_mode("grid")
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

        def fetcher(offset: int, limit: int):
            items, marker = resources.flat_files(
                media_type="image", offset=offset, limit=limit
            )
            return items, marker

        self.model.set_fetcher(fetcher)
        self._update_states()

    def _update_states(self) -> None:
        if self.model.loading and not self.model.rowCount():
            self.stack.setCurrentWidget(self.loading_label)
        elif not self.model.rowCount():
            self.stack.setCurrentWidget(self.empty_label)
        else:
            self.stack.setCurrentWidget(self.view)
        self.counter.setText(i18n.tr("photos.count", n=self.model.rowCount()))

    def _open(self, resource: Resource) -> None:
        index = 0
        items = self.model.items
        for i, r in enumerate(items):
            if r.path == resource.path:
                index = i
                break
        viewer = PhotoViewer(items, index, self._thumbs, self)
        viewer.exec()

    def _context_menu(self, global_pos, resources: list[Resource]) -> None:
        if not resources:
            return
        single = resources[0] if len(resources) == 1 else None
        menu = QMenu(self)
        if single is not None:
            act_open = QAction(i18n.tr("ctx.open"), menu)
            act_open.triggered.connect(lambda: self._open(single))
            menu.addAction(act_open)
            act_save = QAction(i18n.tr("ctx.download"), menu)
            res_api = self._accounts.resources
            if res_api is not None:
                act_save.triggered.connect(
                    lambda: openers.open_resource(self, res_api, single)
                )
            menu.addAction(act_save)
            menu.addSeparator()
            act_props = QAction(i18n.tr("ctx.properties"), menu)
            act_props.triggered.connect(
                lambda: PropertiesDialog(single, self._thumbs, self).exec()
            )
            menu.addAction(act_props)
        menu.exec(global_pos)

    def retranslate(self) -> None:
        self.title.setText(i18n.tr("photos.title"))
        self.loading_label.setText(i18n.tr("photos.loading"))
        self.empty_label.setText(i18n.tr("photos.empty"))
