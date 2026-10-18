"""Страница «Файлы»: тулбар + листинг + контекстное меню + все операции.

Все файловые операции — через REST; после каждой — перезагрузка папки и
обновление квоты. Критические — с подтверждением.
"""

from __future__ import annotations

from PySide6.QtCore import QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QLabel,
    QMenu,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ydx.core.api_resources import ResourcesApi
from ydx.core.errors import ApiError
from ydx.core.models import Resource
from ydx.core.operations import wait_operation
from ydx.services import i18n, openers
from ydx.services.accounts import AccountManager
from ydx.services.settings import AppSettings
from ydx.services.thumbs import ThumbService
from ydx.services.workers import run_in_thread
from ydx.ui.dialogs.prompts import FolderPickerDialog, PropertiesDialog, ask_name
from ydx.ui.errors_ui import confirm, show_api_error
from ydx.ui.toolbar import FilesToolbar
from ydx.ui.views.files_model import FilesModel, ResourceRole
from ydx.ui.views.files_view import FilesView


class FilesFilterModel(QSortFilterProxyModel):
    """Клиентский фильтр по подстроке в имени (поиск в текущей папке)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._needle = ""

    def set_needle(self, needle: str) -> None:
        self._needle = needle.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:  # noqa: N802
        if not self._needle:
            return True
        index = self.sourceModel().index(source_row, 0, source_parent)
        resource = index.data(ResourceRole)
        return resource is not None and self._needle in (resource.name or "").lower()


class FilesPage(QWidget):
    """Параметры: settings, accounts (для api/квоты), thumbs."""

    def __init__(
        self, settings: AppSettings, accounts: AccountManager, thumbs: ThumbService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._accounts = accounts
        self._thumbs = thumbs
        self._history: list[str] = []
        self._current = "disk:/"
        self._filter = ""
        self._shown_once = False

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self.toolbar = FilesToolbar()
        col.addWidget(self.toolbar)

        self.stack = QStackedWidget()
        self.model = FilesModel(self)
        self.filter_model = FilesFilterModel(self)
        self.filter_model.setSourceModel(self.model)
        self.view = FilesView(self.filter_model, thumbs)
        self.stack.addWidget(self.view)          # 0 — список
        self.loading_label = QLabel(i18n.tr("files.loading"))
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.loading_label)  # 1 — загрузка
        self.empty_label = QLabel(i18n.tr("files.empty_folder"))
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.empty_label)    # 2 — пусто/нет совпадений
        col.addWidget(self.stack, 1)

        self._mode = settings.get("view_mode")
        self._sort = settings.get("sort")
        self.view.set_mode(self._mode)
        self.toolbar.set_mode(self._mode)
        self.toolbar.set_sort(self._sort)

        # --- связи ---
        self.toolbar.back_clicked.connect(self._go_back)
        self.toolbar.breadcrumbs.path_changed.connect(self.load_path)
        self.toolbar.search_changed.connect(self._set_filter)
        self.toolbar.view_mode_changed.connect(self._set_mode)
        self.toolbar.sort_changed.connect(self._set_sort)
        self.toolbar.new_folder_clicked.connect(self.new_folder)
        self.view.open_requested.connect(self._open)
        self.view.context_menu_requested.connect(self._context_menu)
        self.view.move_requested.connect(self._move_to)

        self.model.modelReset.connect(self._update_states)
        self.model.rowsInserted.connect(lambda *_: self._update_states())

        i18n.register(self.retranslate)

    # --- навигация ------------------------------------------------------------

    def on_shown(self) -> None:
        if not self._shown_once:
            self._shown_once = True
            self._reload()
        elif self._accounts.resources is not None and self.model.path != self._current:
            self._reload()

    def load_path(self, path: str, remember: bool = True) -> None:
        if remember and path != self._current:
            self._history.append(self._current)
        self._current = path
        self.toolbar.breadcrumbs.set_path(path)
        self.toolbar.set_back_enabled(bool(self._history))
        self._reload()

    def _go_back(self) -> None:
        if self._history:
            prev = self._history.pop()
            self._current = prev
            self.toolbar.breadcrumbs.set_path(prev)
            self.toolbar.set_back_enabled(bool(self._history))
            self._reload()

    def _reload(self) -> None:
        self.model.set_api(self._accounts.resources)
        if self._accounts.resources is None:
            return
        sort = self._sort
        if sort == "modified":
            sort = "-modified"      # новые сверху, как в веб-интерфейсе
        elif sort == "size":
            sort = "-size"
        self.stack.setCurrentWidget(self.loading_label)
        self.model.load(self._current, sort)

    def _set_filter(self, text: str) -> None:
        self._filter = text.strip().lower()
        self.filter_model.set_needle(text)
        self._update_states()

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._settings.set("view_mode", mode)
        self.view.set_mode(mode)
        self.toolbar.set_mode(mode)

    def _set_sort(self, value: str) -> None:
        self._sort = value
        self._settings.set("sort", value)
        self._reload()

    def _update_states(self) -> None:
        total = self.model.rowCount()
        visible = self.filter_model.rowCount()
        if self.model.loading and total == 0:
            self.stack.setCurrentWidget(self.loading_label)
        elif visible == 0:
            self.empty_label.setText(i18n.tr("files.empty_folder"))
            self.stack.setCurrentWidget(self.empty_label)
        else:
            self.stack.setCurrentWidget(self.view)

    # --- действия --------------------------------------------------------------

    def _open(self, resource: Resource) -> None:
        if resource.is_dir:
            self.load_path(resource.path)
            return
        resources = self._accounts.resources
        if resources is not None:
            openers.open_resource(self, resources, resource)

    def new_folder(self) -> None:
        name = ask_name(self, i18n.tr("files.new_folder"), i18n.tr("files.new_folder_label"))
        if not name:
            return

        def job():
            res = self._require_api()
            res.mkdir(f"{self._current.rstrip('/')}/{name}")
            return name

        run_in_thread(
            job,
            lambda _n: self._after_op(),
            lambda exc: show_api_error(self, exc),
        )

    def rename(self, resource: Resource) -> None:
        new_name = ask_name(
            self, i18n.tr("ctx.rename"), i18n.tr("files.rename_label"), resource.name
        )
        if not new_name or new_name == resource.name:
            return
        parent = resource.path.rsplit("/", 1)[0] or "disk:/"
        dst = f"{parent}/{new_name}"

        def job():
            res = self._require_api()
            op = res.move(resource.path, dst)
            if op is not None:
                wait_operation(self._accounts.api, op)
            return dst

        run_in_thread(job, lambda _p: self._after_op(), lambda exc: show_api_error(self, exc))

    def delete(self, resources: list[Resource]) -> None:
        if not resources:
            return
        if len(resources) == 1:
            text = i18n.tr("files.delete_confirm_one", name=resources[0].name)
        else:
            text = i18n.tr("files.delete_confirm_many", n=len(resources))
        if not confirm(self, text):
            return

        def job():
            api_res = self._require_api()
            for r in resources:
                op = api_res.delete(r.path)
                if op is not None:
                    wait_operation(self._accounts.api, op)
            return len(resources)

        run_in_thread(job, lambda _n: self._after_op(), lambda exc: show_api_error(self, exc))

    def publish(self, resource: Resource) -> None:
        def job():
            return self._require_api().publish(resource.path)

        def done(updated: Resource) -> None:
            QGuiApplication.clipboard().setText(updated.public_url or "")
            self._after_op()

        run_in_thread(
            job, done,
            lambda exc: show_api_error(self, exc, i18n.tr("files.link_failed_publish")),
        )

    def unpublish(self, resource: Resource) -> None:
        run_in_thread(
            lambda: self._require_api().unpublish(resource.path),
            lambda _r: self._after_op(),
            lambda exc: show_api_error(self, exc, i18n.tr("files.link_failed_publish")),
        )

    def _pick_folder(self, title: str) -> str | None:
        resources = self._accounts.resources
        if resources is None:
            return None
        dlg = FolderPickerDialog(resources, self._current, self)
        dlg.setWindowTitle(title)
        if dlg.exec() and dlg.selected_path:
            return dlg.selected_path
        return None

    def _move_to(self, paths: list[str], target_dir: str) -> None:
        self._transfer_paths(paths, target_dir, move=True)

    def move(self, resources: list[Resource]) -> None:
        target = self._pick_folder(i18n.tr("files.move_title"))
        if not target:
            return
        self._transfer_paths([r.path for r in resources], target, move=True)

    def copy(self, resources: list[Resource]) -> None:
        target = self._pick_folder(i18n.tr("files.copy_title"))
        if not target:
            return
        self._transfer_paths([r.path for r in resources], target, move=False)

    def _transfer_paths(self, paths: list[str], target: str, move: bool) -> None:
        def job():
            api_res = self._require_api()
            for src in paths:
                name = src.rsplit("/", 1)[-1]
                dst = f"{target.rstrip('/')}/{name}"
                op = api_res.move(src, dst) if move else api_res.copy(src, dst)
                if op is not None:
                    wait_operation(self._accounts.api, op)
            return len(paths)

        run_in_thread(job, lambda _n: self._after_op(), lambda exc: show_api_error(self, exc))

    def properties(self, resource: Resource) -> None:
        PropertiesDialog(resource, self._thumbs, self).exec()

    def _after_op(self) -> None:
        self._reload()
        self._accounts.refresh_quota()

    def _require_api(self) -> ResourcesApi:
        resources = self._accounts.resources
        if resources is None:
            raise ApiError(401, "NoAccount", "not signed in")
        return resources

    # --- контекстное меню ----------------------------------------------------------

    def _context_menu(self, global_pos, resources: list[Resource]) -> None:
        menu = QMenu(self)
        if not resources:
            act_new = QAction(i18n.tr("ctx.new_folder"), menu)
            act_new.triggered.connect(self.new_folder)
            menu.addAction(act_new)
            menu.exec(global_pos)
            return

        single = resources[0] if len(resources) == 1 else None
        act_open = QAction(i18n.tr("ctx.open"), menu)
        act_open.triggered.connect(lambda: self._open(resources[0]))
        menu.addAction(act_open)

        if single is not None and single.public_url:
            act_copy = QAction(i18n.tr("ctx.copy_link"), menu)
            act_copy.triggered.connect(lambda: self._copy_public_url(single))
            menu.addAction(act_copy)
            act_unpub = QAction(i18n.tr("ctx.unpublish"), menu)
            act_unpub.triggered.connect(lambda: self.unpublish(single))
            menu.addAction(act_unpub)
        elif single is not None:
            act_pub = QAction(i18n.tr("ctx.copy_link"), menu)
            act_pub.triggered.connect(lambda: self.publish(single))
            menu.addAction(act_pub)

        menu.addSeparator()
        if single is not None:
            act_rename = QAction(i18n.tr("ctx.rename"), menu)
            act_rename.triggered.connect(lambda: self.rename(single))
            menu.addAction(act_rename)
        act_move = QAction(i18n.tr("ctx.move"), menu)
        act_move.triggered.connect(lambda: self.move(resources))
        menu.addAction(act_move)
        act_copy_files = QAction(i18n.tr("ctx.copy"), menu)
        act_copy_files.triggered.connect(lambda: self.copy(resources))
        menu.addAction(act_copy_files)
        menu.addSeparator()
        act_del = QAction(i18n.tr("ctx.delete"), menu)
        act_del.triggered.connect(lambda: self.delete(resources))
        menu.addAction(act_del)
        if single is not None:
            menu.addSeparator()
            act_props = QAction(i18n.tr("ctx.properties"), menu)
            act_props.triggered.connect(lambda: self.properties(single))
            menu.addAction(act_props)
        menu.exec(global_pos)

    def _copy_public_url(self, resource: Resource) -> None:
        if resource.public_url:
            QGuiApplication.clipboard().setText(resource.public_url)

    # --- прочее --------------------------------------------------------------------

    def retranslate(self) -> None:
        self.loading_label.setText(i18n.tr("files.loading"))
        self.empty_label.setText(i18n.tr("files.empty_folder"))
