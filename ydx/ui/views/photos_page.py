"""Страница «Фото»: все фото + альбомы (группировка по папкам Диска)."""

from __future__ import annotations

from PySide6.QtCore import Qt
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
from ydx.services import i18n, openers
from ydx.services.accounts import AccountManager
from ydx.services.thumbs import ThumbService
from ydx.ui.dialogs.prompts import PropertiesDialog
from ydx.ui.views.files_view import FilesView
from ydx.ui.views.resource_list import ResourceListModel
from ydx.ui.views.viewer import PhotoViewer


def group_albums(resources: list[Resource]) -> list[dict]:
    """Папки с фотографиями → список альбомов (имя, путь, счётчик, обложка)."""
    by_folder: dict[str, list[Resource]] = {}
    for r in resources:
        if r.is_dir:
            continue
        folder = r.path.rsplit("/", 1)[0] or "disk:/"
        by_folder.setdefault(folder, []).append(r)
    albums = []
    for folder, photos in by_folder.items():
        is_root = folder.rstrip("/") in ("disk:", "")      # «disk:» и «disk:/» — корень
        name = i18n.tr("photos.root_album") if is_root else folder.rstrip("/").rsplit("/", 1)[-1]
        albums.append({
            "name": name,
            "path": folder,
            "count": len(photos),
            "photos": photos,
        })
    albums.sort(key=lambda a: (-a["count"], a["name"].lower()))
    return albums


class PhotosPage(QWidget):
    def __init__(self, accounts: AccountManager, thumbs: ThumbService,
                 state_service=None, parent=None) -> None:
        super().__init__(parent)
        self._accounts = accounts
        self._thumbs = thumbs
        self._state = state_service
        self._albums: list[dict] = []

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        header = QWidget()
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 12, 16, 8)
        hlay.setSpacing(10)
        self.title = QLabel()
        self.title.setObjectName("dialogTitle")
        hlay.addWidget(self.title)
        self.counter = QLabel()
        self.counter.setObjectName("dialogHint")
        hlay.addWidget(self.counter)
        hlay.addStretch(1)

        self.back_btn = QPushButton()
        self.back_btn.setProperty("class", "flat")
        self.back_btn.clicked.connect(self._show_albums)
        self.back_btn.hide()
        hlay.addWidget(self.back_btn)

        # сортировка фото: дата / размер / имя (прокси вешается после создания вида)
        from PySide6.QtWidgets import QComboBox

        self.sort_combo = QComboBox()
        for value, key in (("modified", "photos.sort.date"),
                           ("size", "photos.sort.size"),
                           ("name", "photos.sort.name")):
            self.sort_combo.addItem(i18n.tr(key), value)
        hlay.addWidget(self.sort_combo)

        self.all_btn = QPushButton()
        self.all_btn.setCheckable(True)
        self.albums_btn = QPushButton()
        self.albums_btn.setCheckable(True)
        self.all_btn.clicked.connect(lambda: self._switch_mode("all"))
        self.albums_btn.clicked.connect(lambda: self._switch_mode("albums"))
        hlay.addWidget(self.all_btn)
        hlay.addWidget(self.albums_btn)
        col.addWidget(header)

        self.stack = QStackedWidget()

        # сетка фото: все (ленивая модель) или один альбом (статическая)
        self.model = ResourceListModel(self)
        self.album_photos_model = ResourceListModel(self)

        from ydx.ui.views.resource_list import ResourceSortProxy

        self.sort_proxy = ResourceSortProxy(self)
        self.sort_proxy.setSourceModel(self.model)
        self.view = FilesView(self.sort_proxy, thumbs)
        self.view.set_mode("grid")
        self.stack.addWidget(self.view)
        self.sort_combo.currentIndexChanged.connect(
            lambda idx: self.sort_proxy.set_sort_key(self.sort_combo.itemData(idx))
        )

        # сетка альбомов
        self.albums_model = ResourceListModel(self)
        self.albums_view = FilesView(self.albums_model, thumbs)
        self.albums_view.set_mode("grid")
        self.stack.addWidget(self.albums_view)

        self.loading_label = QLabel()
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.loading_label)
        self.empty_label = QLabel()
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.empty_label)
        col.addWidget(self.stack, 1)

        self.view.open_requested.connect(self._open_photo)
        self.view.context_menu_requested.connect(self._context_menu)
        self.albums_view.open_requested.connect(self._open_album)
        self.model.modelReset.connect(self._update_states)
        self.model.rowsInserted.connect(lambda *_: self._update_states())

        # значки состояний (offline/synced/cloud/error) — как в Файлах
        from PySide6.QtCore import QTimer

        self._badges_timer = QTimer(self)
        self._badges_timer.setSingleShot(True)
        self._badges_timer.setInterval(80)
        self._badges_timer.timeout.connect(self._refresh_badges)
        self.model.rowsInserted.connect(lambda *_: self._badges_timer.start())
        if self._state is not None:
            self._state.states_changed.connect(self._badges_timer.start)

        i18n.register(self.retranslate)
        self.retranslate()
        self._switch_mode("all")

    # --- режимы ------------------------------------------------------------------

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

    def _switch_mode(self, mode: str) -> None:
        self._mode = mode
        self.all_btn.setChecked(mode == "all")
        self.albums_btn.setChecked(mode == "albums")
        self.back_btn.setVisible(mode == "album")
        if mode == "albums":
            self._show_albums()
        else:
            # вернуть ленивую модель «все фото» (после альбома view смотрел на подмножество)
            if self.view.model() is not self.sort_proxy:
                self.view.setModel(self.sort_proxy)
            self._update_states()

    def _show_albums(self) -> None:
        self._albums = group_albums(self.model.items)
        pseudo = [
            Resource(name=a["name"], type="dir", path=a["path"], total=a["count"])
            for a in self._albums
        ]
        self.albums_model.set_items(pseudo)
        if pseudo:
            self.stack.setCurrentWidget(self.albums_view)
            self.counter.setText(i18n.tr("photos.album_count", n=len(self.model.items)))
        else:
            self.empty_label.setText(i18n.tr("photos.empty"))
            self.stack.setCurrentWidget(self.empty_label)

    def _open_album(self, resource: Resource) -> None:
        photos = []
        for album in self._albums:
            if album["path"] == resource.path:
                photos = album["photos"]
                break
        self.album_photos_model.set_items(photos)
        self.view.setModel(self.album_photos_model)
        self._mode = "album"
        self.back_btn.setVisible(True)
        self.all_btn.setChecked(False)
        self.albums_btn.setChecked(False)
        self.stack.setCurrentWidget(self.view)
        self.counter.setText(
            f"{resource.name} · " + i18n.tr("photos.album_count", n=len(photos))
        )

    def _refresh_badges(self) -> None:
        if self._state is None:
            return
        items = self.model.items

        def job():
            return self._state.compute(items)[0]

        def done(states) -> None:
            self.view.set_item_states(states)

        from ydx.services.workers import run_in_thread

        run_in_thread(job, done, lambda _e: None)

    def _update_states(self) -> None:
        if self._mode == "albums":
            return
        if self.model.loading and not self.model.rowCount():
            self.stack.setCurrentWidget(self.loading_label)
        elif not self.model.rowCount():
            self.stack.setCurrentWidget(self.empty_label)
        else:
            self.stack.setCurrentWidget(self.view)
        self.counter.setText(i18n.tr("photos.count", n=self.model.rowCount()))

    # --- просмотр --------------------------------------------------------------------

    def _open_photo(self, resource: Resource) -> None:
        items = (
            self.model.items if self.view.model() is self.sort_proxy
            else self.album_photos_model.items
        )
        index = 0
        for i, r in enumerate(items):
            if r.path == resource.path:
                index = i
                break
        viewer = PhotoViewer(items, index, self._thumbs, self)
        viewer.exec()

    def _context_menu(self, global_pos, resources: list[Resource]) -> None:
        if not resources or self._mode == "albums":
            return
        single = resources[0] if len(resources) == 1 else None
        if single is None:
            return
        menu = QMenu(self)
        act_open = QAction(i18n.tr("ctx.open"), menu)
        act_open.triggered.connect(lambda: self._open_photo(single))
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
        self.all_btn.setText(i18n.tr("photos.all"))
        self.albums_btn.setText(i18n.tr("photos.albums"))
        self.back_btn.setText(i18n.tr("photos.back_albums"))
        self.counter.setText(i18n.tr("photos.count", n=self.model.rowCount()))
        for value, key in (("modified", "photos.sort.date"),
                           ("size", "photos.sort.size"),
                           ("name", "photos.sort.name")):
            idx = [self.sort_combo.itemData(i) for i in range(3)].index(value)
            self.sort_combo.setItemText(idx, i18n.tr(key))
