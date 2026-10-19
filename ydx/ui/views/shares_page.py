"""Страница «Общие»: опубликованные вами ресурсы, управление ссылками."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QGuiApplication, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ydx.core.models import Resource
from ydx.services import i18n
from ydx.services.accounts import AccountManager
from ydx.services.thumbs import ThumbService
from ydx.services.workers import run_in_thread
from ydx.ui.dialogs.prompts import PropertiesDialog
from ydx.ui.errors_ui import confirm, show_api_error
from ydx.ui.views.files_view import FilesView
from ydx.ui.views.resource_list import ResourceListModel


class SharesPage(QWidget):
    def __init__(self, accounts: AccountManager, thumbs: ThumbService,
                 state_service=None, parent=None) -> None:
        super().__init__(parent)
        self._accounts = accounts
        self._thumbs = thumbs
        self._state = state_service
        self._shown_once = False

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        header = QWidget()
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 12, 16, 8)
        self.title = QLabel()
        self.title.setObjectName("dialogTitle")
        hlay.addWidget(self.title)
        self.hint = QLabel()
        self.hint.setObjectName("dialogHint")
        hlay.addWidget(self.hint)
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

        # значки состояний — как в Файлах
        from PySide6.QtCore import QTimer

        self._badges_timer = QTimer(self)
        self._badges_timer.setSingleShot(True)
        self._badges_timer.setInterval(80)
        self._badges_timer.timeout.connect(self._refresh_badges)
        if self._state is not None:
            self._state.states_changed.connect(self._badges_timer.start)
        self._badges_timer.start()

    def _refresh_badges(self) -> None:
        if self._state is None:
            return

        def job():
            return self._state.compute(self.model.items)[0]

        def done(states) -> None:
            self.view.set_item_states(states)

        run_in_thread(job, done, lambda _e: None)

        i18n.register(self.retranslate)
        self.retranslate()

    def on_shown(self) -> None:
        resources = self._accounts.resources
        if resources is None:
            return

        def fetcher(offset: int, limit: int):
            return resources.list_public(offset=offset, limit=limit)

        self.model.set_fetcher(fetcher)
        self._update_states()

    def _update_states(self) -> None:
        if self.model.loading and not self.model.rowCount():
            self.stack.setCurrentWidget(self.loading_label)
        elif not self.model.rowCount():
            self.stack.setCurrentWidget(self.empty_label)
        else:
            self.stack.setCurrentWidget(self.view)

    def _open(self, resource: Resource) -> None:
        if resource.public_url:
            QDesktopServices.openUrl(QUrl(resource.public_url))

    def _unpublish(self, resource: Resource) -> None:
        if not confirm(self, i18n.tr("shares.unpublish_confirm", name=resource.name)):
            return
        run_in_thread(
            lambda: self._accounts.resources.unpublish(resource.path),
            lambda _r: self.on_shown(),
            lambda exc: show_api_error(self, exc),
        )

    def _context_menu(self, global_pos, resources: list[Resource]) -> None:
        if not resources:
            return
        single = resources[0] if len(resources) == 1 else None
        menu = QMenu(self)
        if single is not None and single.public_url:
            act_copy = QAction(i18n.tr("ctx.copy_link"), menu)
            act_copy.triggered.connect(
                lambda: QGuiApplication.clipboard().setText(single.public_url or "")
            )
            menu.addAction(act_copy)
            act_browser = QAction(i18n.tr("ctx.open_in_browser"), menu)
            act_browser.triggered.connect(lambda: self._open(single))
            menu.addAction(act_browser)
            act_unpub = QAction(i18n.tr("ctx.unpublish"), menu)
            act_unpub.triggered.connect(lambda: self._unpublish(single))
            menu.addAction(act_unpub)
        if single is not None:
            menu.addSeparator()
            act_props = QAction(i18n.tr("ctx.properties"), menu)
            act_props.triggered.connect(
                lambda: PropertiesDialog(single, self._thumbs, self).exec()
            )
            menu.addAction(act_props)
        menu.exec(global_pos)

    def retranslate(self) -> None:
        self.title.setText(i18n.tr("shares.title"))
        self.hint.setText(i18n.tr("shares.hint"))
        self.loading_label.setText(i18n.tr("files.loading"))
        self.empty_label.setText(i18n.tr("shares.empty"))
