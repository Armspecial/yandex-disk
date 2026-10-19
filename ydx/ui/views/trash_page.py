"""Страница «Корзина»: просмотр (с заходом в подпапки), восстановление,
удаление навсегда, очистка целиком.

Пути в корзине имеют суффиксы вида trash:/foo_123456 — работаем с ними как есть.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ydx.core.models import Resource
from ydx.core.operations import wait_operation
from ydx.services import i18n
from ydx.services.accounts import AccountManager
from ydx.services.thumbs import ThumbService
from ydx.services.workers import run_in_thread
from ydx.ui.dialogs.prompts import PropertiesDialog
from ydx.ui.errors_ui import confirm, show_api_error
from ydx.ui.widgets import icon
from ydx.ui.views.files_view import FilesView
from ydx.ui.views.resource_list import ResourceListModel


class TrashPage(QWidget):
    def __init__(self, accounts: AccountManager, thumbs: ThumbService, parent=None) -> None:
        super().__init__(parent)
        self._accounts = accounts
        self._thumbs = thumbs
        self._path = "/"
        self._history: list[str] = []
        self._shown_once = False

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        header = QWidget()
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 12, 16, 8)
        self.back_btn = QPushButton()
        self.back_btn.setProperty("class", "icon")
        self.back_btn.setIcon(icon("chevron-left"))
        self.back_btn.setToolTip(i18n.tr("toolbar.back"))
        self.back_btn.setEnabled(False)
        self.back_btn.clicked.connect(self._go_back)
        hlay.addWidget(self.back_btn)
        self.title = QLabel()
        self.title.setObjectName("dialogTitle")
        hlay.addWidget(self.title)
        hlay.addStretch(1)
        self.empty_all_btn = QPushButton()
        self.empty_all_btn.setProperty("class", "danger")
        self.empty_all_btn.clicked.connect(self._empty_all)
        hlay.addWidget(self.empty_all_btn)
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

    # --- загрузка -------------------------------------------------------------

    def on_shown(self) -> None:
        resources = self._accounts.resources
        if resources is None:
            return
        self.model.set_fetcher(
            lambda offset, limit: resources.trash_list(path=self._path, offset=offset, limit=limit)
        )
        self._update_states()

    def _open(self, resource: Resource) -> None:
        if resource.is_dir:
            self._history.append(self._path)
            self.back_btn.setEnabled(True)
            self._path = resource.path.removeprefix("trash:")
            self.on_shown()
            self.title.setText(
                f"{i18n.tr('trash.title')} — {resource.name}"
            )

    def _go_back(self) -> None:
        if self._history:
            self._path = self._history.pop()
            self.back_btn.setEnabled(bool(self._history))
            self.title.setText(i18n.tr("trash.title"))
            self.on_shown()

    def _update_states(self) -> None:
        if self.model.loading and not self.model.rowCount():
            self.stack.setCurrentWidget(self.loading_label)
        elif not self.model.rowCount():
            self.stack.setCurrentWidget(self.empty_label)
        else:
            self.stack.setCurrentWidget(self.view)

    # --- операции ----------------------------------------------------------------

    def _restore(self, resources: list[Resource]) -> None:
        def job():
            res = self._accounts.resources
            for r in resources:
                op = res.trash_restore(r.path)
                if op is not None:
                    wait_operation(self._accounts.api, op)
            return len(resources)

        run_in_thread(
            job,
            lambda _n: (self.on_shown(), self._accounts.refresh_quota()),
            lambda exc: show_api_error(self, exc),
        )

    def _delete_forever(self, resources: list[Resource]) -> None:
        if len(resources) == 1:
            text = i18n.tr("trash.delete_one_confirm", name=resources[0].name)
        else:
            text = i18n.tr("trash.empty_confirm")
        if not confirm(self, text):
            return

        def job():
            res = self._accounts.resources
            for r in resources:
                op = res.trash_delete(r.path)
                if op is not None:
                    wait_operation(self._accounts.api, op)
            return len(resources)

        run_in_thread(
            job,
            lambda _n: (self.on_shown(), self._accounts.refresh_quota()),
            lambda exc: show_api_error(self, exc),
        )

    def _empty_all(self) -> None:
        if not confirm(self, i18n.tr("trash.empty_confirm")):
            return

        def job():
            res = self._accounts.resources
            op = res.trash_delete(None)
            if op is not None:
                wait_operation(self._accounts.api, op)

        run_in_thread(
            job,
            lambda _n: (self.on_shown(), self._accounts.refresh_quota()),
            lambda exc: show_api_error(self, exc),
        )

    def _context_menu(self, global_pos, resources: list[Resource]) -> None:
        if not resources:
            return
        menu = QMenu(self)
        act_restore = QAction(i18n.tr("trash.restore"), menu)
        act_restore.triggered.connect(lambda: self._restore(resources))
        menu.addAction(act_restore)
        act_del = QAction(i18n.tr("trash.delete_forever"), menu)
        act_del.triggered.connect(lambda: self._delete_forever(resources))
        menu.addAction(act_del)
        if len(resources) == 1:
            menu.addSeparator()
            act_props = QAction(i18n.tr("ctx.properties"), menu)
            act_props.triggered.connect(lambda: PropertiesDialog(resources[0], self._thumbs, self).exec())
            menu.addAction(act_props)
        menu.exec(global_pos)

    def retranslate(self) -> None:
        self.title.setText(i18n.tr("trash.title"))
        self.empty_all_btn.setText(i18n.tr("trash.empty_all"))
        self.loading_label.setText(i18n.tr("files.loading"))
        self.empty_label.setText(i18n.tr("trash.empty"))
        self.back_btn.setToolTip(i18n.tr("toolbar.back"))
