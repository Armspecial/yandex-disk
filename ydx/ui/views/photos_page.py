"""Страница «Фото»: все фото + альбомы (группировка по папкам Диска)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ydx.constants import PAGE_SIZE
from ydx.core.models import Resource
from ydx.services import i18n, openers
from ydx.services.accounts import AccountManager
from ydx.services.thumbs import ThumbService
from ydx.ui.dialogs.prompts import PropertiesDialog
from ydx.ui.views.files_view import FilesView
from ydx.ui.views.photo_timeline import PhotoTimelineView
from ydx.ui.views.resource_list import ResourceListModel
from ydx.ui.views.viewer import PhotoViewer, VideoViewer, viewer_kind


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
        stripped = folder.rstrip("/")
        if stripped == "photounlim:":                 # лента безлимитной автозагрузки
            name = i18n.tr("photos.unlim_album")
        elif stripped in ("disk:", ""):               # «disk:» и «disk:/» — корень
            name = i18n.tr("photos.root_album")
        else:
            name = stripped.rsplit("/", 1)[-1]
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
                 state_service=None, unlim=None, engine=None, settings=None,
                 parent=None) -> None:
        super().__init__(parent)
        self._accounts = accounts
        self._thumbs = thumbs
        self._state = state_service
        self._unlim = unlim                    # UnlimService | None (безлимит)
        self._engine = engine                  # TransferEngine | None (скачивание)
        self._settings = settings
        self._unlim_error: Exception | None = None
        self._albums: list[dict] = []
        self._media = "image"           # image | video — переключатель в шапке
        self._collected: dict[str, list[Resource]] = {}   # кэш сессии по типу медиа
        self._collect_id = 0            # инкремент рвёт идущий сбор (смена источника)

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

        # фото или видео: автозагрузка телефона даёт и то и другое
        self.photo_btn = QPushButton()
        self.video_btn = QPushButton()
        for btn, kind in ((self.photo_btn, "image"), (self.video_btn, "video")):
            btn.setCheckable(True)
            btn.setProperty("class", "flat")
            btn.clicked.connect(lambda _=False, k=kind: self._set_media(k))
        self.photo_btn.setChecked(True)
        hlay.addWidget(self.photo_btn)
        hlay.addWidget(self.video_btn)

        # скачать выбранное (фото/видео, включая безлимит) — активна при выделении
        self.download_btn = QPushButton()
        self.download_btn.setProperty("class", "primary")
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(
            lambda: self._download_selected(self._selected())
        )
        hlay.addWidget(self.download_btn)
        col.addWidget(header)

        self.stack = QStackedWidget()

        # модель сбора (растёт по мере обхода) и статическая модель альбома
        self.model = ResourceListModel(self)
        self.album_photos_model = ResourceListModel(self)

        from ydx.ui.views.resource_list import ResourceSortProxy

        # сетка одного альбома (сортировка из селектора в шапке)
        self.sort_proxy = ResourceSortProxy(self)
        self.sort_proxy.setSourceModel(self.album_photos_model)
        self.view = FilesView(self.sort_proxy, thumbs)
        self.view.set_mode("grid")
        self.stack.addWidget(self.view)
        self.sort_combo.currentIndexChanged.connect(
            lambda idx: self.sort_proxy.set_sort_key(self.sort_combo.itemData(idx))
        )
        self.sort_proxy.set_sort_key(self.sort_combo.currentData())   # «Дата» по умолчанию

        # «Все фото»: таймлайн с заголовками дней, новые сверху
        self.timeline = PhotoTimelineView(thumbs)
        self.stack.addWidget(self.timeline)

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

        # полоса состояния безлимита: ошибка ленты или подсказка «подключите куки»
        self.unlim_strip = QLabel()
        self.unlim_strip.setObjectName("statusChip")
        self.unlim_strip.setWordWrap(True)
        self.unlim_strip.setMargin(8)
        self.unlim_strip.setCursor(Qt.CursorShape.PointingHandCursor)
        self.unlim_strip.mousePressEvent = lambda _e: self._open_unlim_dialog()
        self.unlim_strip.hide()
        col.addWidget(self.unlim_strip)

        self.view.open_requested.connect(self._open_photo)
        self.view.context_menu_requested.connect(self._context_menu)
        self.timeline.open_requested.connect(self._open_photo)
        self.timeline.context_menu_requested.connect(self._context_menu)
        self.albums_view.open_requested.connect(self._open_album)
        self.model.modelReset.connect(self._update_states)
        self.model.rowsInserted.connect(lambda *_: self._update_states())
        # таймлайн перестраивается по мере сбора (сортирует и группирует сам)
        self.model.rowsInserted.connect(
            lambda *_: self.timeline.set_photos(self.model.items)
        )
        self.model.modelReset.connect(lambda: self.timeline.set_photos([]))

        # кнопка «Скачать» живёт выделением текущей вьюхи
        self._wire_selection(self.view)
        self._wire_selection(self.timeline)

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

        # смена аккаунта — сбросить сессионный кэш и остановить идущий сбор
        self._accounts.active_changed.connect(lambda _a: self._invalidate())
        # куки безлимита сменились — пересобрать ленту
        if self._unlim is not None:
            self._unlim.cookies_changed.connect(lambda: self._invalidate())

    # --- режимы ------------------------------------------------------------------

    def on_shown(self) -> None:
        resources = self._accounts.resources
        if resources is None:
            return
        cached = self._collected.get(self._media)
        if cached is not None:
            self.model.set_items(cached)
            self._update_states()
            return

        # сбор из двух источников (плоский список + автозагрузка) порциями:
        # страницы тянутся по одной в пуле потоков, модель растёт инкрементально
        self._collect_id += 1
        collect_id = self._collect_id
        self._unlim_error = None
        self.model.begin_collect()
        pages = self._merged_iter(resources)

        def pull() -> list[Resource]:
            batch: list[Resource] = []
            for item in pages:                 # генератор отдаёт по одному
                batch.append(item)
                if len(batch) >= PAGE_SIZE:
                    break
            return batch

        def done(batch: list[Resource]) -> None:
            if collect_id != self._collect_id:
                return                       # источник уже сменили — не продолжаем
            if batch:
                self.model.add_items(batch)
                self._collect_next(pull, done, failed)
            else:
                self.model.end_collect()
                self._collected[self._media] = self.model.items
                self._update_states()
                self._update_unlim_strip()

        def failed(_exc: Exception) -> None:
            if collect_id != self._collect_id:
                return
            self.model.end_collect()          # показываем то, что успели собрать
            self._update_states()
            self._update_unlim_strip()

        self._collect_next(pull, done, failed)

    def _merged_iter(self, resources):
        """Лента: сначала весь Диск (REST), затем безлимит (внутреннее API).

        Ошибка безлимита не должна ронять REST-часть — запоминаем её
        для полосы состояния и заканчиваем выдачу.
        """
        yield from resources.iter_media(self._media)
        if (self._unlim is None or not self._unlim.enabled
                or not self._unlim.has_cookies):
            return
        try:
            yield from self._unlim.iter_media(self._media)
        except Exception as exc:  # noqa: BLE001 — лента важнее
            self._unlim_error = exc

    def _collect_next(self, pull, done, failed=None) -> None:
        from ydx.services.workers import run_in_thread

        run_in_thread(pull, done, failed or (lambda _e: None))

    def _invalidate(self) -> None:
        self._collect_id += 1
        self._collected.clear()
        if self.isVisible():
            self.on_shown()

    def _set_media(self, kind: str) -> None:
        """Переключение «Фото / Видео»: смена источника и возврат в «Все»."""
        if kind == self._media:
            return
        self._media = kind
        self.photo_btn.setChecked(kind == "image")
        self.video_btn.setChecked(kind == "video")
        self._switch_mode("all")
        self.on_shown()

    def _switch_mode(self, mode: str) -> None:
        self._mode = mode
        self.all_btn.setChecked(mode == "all")
        self.albums_btn.setChecked(mode == "albums")
        self.back_btn.setVisible(mode == "album")
        self.sort_combo.setVisible(mode == "album")   # сортировка — только в альбоме
        self.download_btn.setEnabled(False)
        self.download_btn.setVisible(mode in ("all", "album"))
        if mode == "albums":
            self._show_albums()
        else:
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
        self._wire_selection(self.view)      # setModel создаёт новый selectionModel
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
            self.timeline.set_item_states(states)

        from ydx.services.workers import run_in_thread

        run_in_thread(job, done, lambda _e: None)

    def _update_states(self) -> None:
        if self._mode == "albums":
            return
        if self.model.loading and not self.model.rowCount():
            self.stack.setCurrentWidget(self.loading_label)
        elif self._mode == "album":
            self.stack.setCurrentWidget(self.view)
        elif self.model.rowCount():
            self.stack.setCurrentWidget(self.timeline)
        else:
            self.stack.setCurrentWidget(self.empty_label)
        self.counter.setText(i18n.tr("photos.count", n=self.model.rowCount()))

    # --- безлимит и скачивание ---------------------------------------------------

    def _wire_selection(self, view) -> None:
        selection = view.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(
                lambda *_: self.download_btn.setEnabled(bool(self._selected()))
            )

    def _selected(self) -> list[Resource]:
        """Выбранное в активной вьюхе: таймлайн в «Все», сетка в альбоме."""
        if self._mode == "all":
            return self.timeline.selected_resources()
        if self._mode == "album":
            return self.view.selected_resources()
        return []

    def _download_selected(self, resources: list[Resource]) -> None:
        """Скачать выделенное: Диск → движок передач, безлимит → по прямым ссылкам."""
        files = [r for r in resources if not r.is_dir]
        if not files:
            return
        from pathlib import Path

        base = Path(self._downloads_dir())
        rest = [r for r in files if not r.path.startswith("photounlim:")]
        unlim_items = [r for r in files if r.path.startswith("photounlim:")]
        if rest:
            if self._engine is not None:
                base.mkdir(parents=True, exist_ok=True)
                from ydx.ui.views.files_page import mirror_local_path

                for r in rest:
                    self._engine.enqueue_download(
                        r, str(mirror_local_path(base, r.path))
                    )
            else:
                openers.open_resource(self, self._accounts.resources, rest[0])
        if unlim_items and self._unlim is not None:
            from ydx.services.workers import run_in_thread

            base.mkdir(parents=True, exist_ok=True)
            run_in_thread(
                lambda: openers.unlim_download(self._unlim, unlim_items, base),
                self._download_report,
                lambda exc: self._download_report(
                    {"done": 0, "skipped": len(unlim_items), "names": []}
                ),
            )

    def _downloads_dir(self) -> str:
        from pathlib import Path

        if self._settings is not None:
            configured = self._settings.get("downloads_dir")
            if configured:
                return configured
        return str(Path.home() / "Yandex Disk")

    def _download_report(self, result: dict) -> None:
        """Итог скачивания безлимита: сколько удалось, что пропущено."""
        text = i18n.tr("unlim.download_summary", done=result["done"])
        if result["skipped"]:
            text += "\n" + i18n.tr("photos.download_no_link", n=result["skipped"])
        QMessageBox.information(self, i18n.tr("app.title"), text)

    def _update_unlim_strip(self) -> None:
        """Полоса под лентой: ошибка безлимита или подсказка подключить куки."""
        if self._unlim is None:
            self.unlim_strip.hide()
            return
        if self._unlim_error is not None:
            from ydx.ui.errors_ui import api_error_text

            self.unlim_strip.setText(
                i18n.tr("unlim.feed_error", error=api_error_text(self._unlim_error))
            )
            self.unlim_strip.show()
        elif self._unlim.enabled and not self._unlim.has_cookies:
            self.unlim_strip.setText(i18n.tr("unlim.connect_hint"))
            self.unlim_strip.show()
        else:
            self.unlim_strip.hide()

    def _open_unlim_dialog(self) -> None:
        if self._settings is None or self._unlim is None:
            return
        from ydx.ui.dialogs.unlim_dlg import UnlimDialog

        UnlimDialog(self._settings, self._unlim, self).exec()
        self._update_unlim_strip()

    # --- просмотр --------------------------------------------------------------------

    def _open_photo(self, resource: Resource) -> None:
        items = (
            self.album_photos_model.items if self._mode == "album"
            else self.model.items
        )
        if viewer_kind(resource) == "video":
            unlim = self._unlim if resource.path.startswith("photounlim:") else None
            VideoViewer(resource, items, self, unlim=unlim).exec()
            return
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
        act_save.triggered.connect(lambda: self._download_selected([single]))
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
        self.photo_btn.setText(i18n.tr("photos.tab_photo"))
        self.video_btn.setText(i18n.tr("photos.tab_video"))
        self.back_btn.setText(i18n.tr("photos.back_albums"))
        self.download_btn.setText(i18n.tr("ctx.download"))
        self.counter.setText(i18n.tr("photos.count", n=self.model.rowCount()))
        for value, key in (("modified", "photos.sort.date"),
                           ("size", "photos.sort.size"),
                           ("name", "photos.sort.name")):
            idx = [self.sort_combo.itemData(i) for i in range(3)].index(value)
            self.sort_combo.setItemText(idx, i18n.tr(key))
