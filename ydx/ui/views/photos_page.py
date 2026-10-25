"""Страница «Фото»: единая лента — фото, видео или всё вместе."""

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

from ydx.core.models import Resource
from ydx.services import i18n, openers
from ydx.services.accounts import AccountManager
from ydx.services.thumbs import ThumbService
from ydx.ui.dialogs.prompts import PropertiesDialog
from ydx.ui.views.photo_timeline import PhotoTimelineView
from ydx.ui.views.resource_list import ResourceListModel
from ydx.ui.views.viewer import PhotoViewer, VideoViewer, viewer_kind


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
        self._media = "image"     # image | video | all — переключатель в шапке
        self._collected: dict[str, list[Resource]] = {}   # кэш сессии по типу медиа
        self._collect_id = 0            # инкремент рвёт идущий сбор (смена источника)
        self._viewer_open = False       # один просмотрщик за раз (не плодим модальные)

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

        # размер плиток таймлайна
        from PySide6.QtWidgets import QSlider

        from ydx.ui.views.photo_timeline import TILE_MAX, TILE_MIN

        self.tile_slider = QSlider(Qt.Orientation.Horizontal)
        self.tile_slider.setRange(TILE_MIN, TILE_MAX)
        self.tile_slider.setSingleStep(8)
        self.tile_slider.setPageStep(32)
        self.tile_slider.setFixedWidth(120)
        hlay.addWidget(self.tile_slider)

        # фото | видео | всё вместе: автозагрузка телефона даёт и фото, и видео
        self.photo_btn = QPushButton()
        self.video_btn = QPushButton()
        self.both_btn = QPushButton()
        for btn, kind in ((self.photo_btn, "image"), (self.video_btn, "video"),
                          (self.both_btn, "all")):
            btn.setCheckable(True)
            btn.setProperty("class", "flat")
            btn.clicked.connect(lambda _=False, k=kind: self._set_media(k))
        self.photo_btn.setChecked(True)
        hlay.addWidget(self.photo_btn)
        hlay.addWidget(self.video_btn)
        hlay.addWidget(self.both_btn)

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

        # модель сбора (растёт по мере обхода); лента — таймлайн с днями
        self.model = ResourceListModel(self)

        # «Все»: таймлайн с заголовками дней, новые сверху
        self.timeline = PhotoTimelineView(thumbs)
        self.stack.addWidget(self.timeline)

        # сохранённый размер плиток + слайдер; переложить ленту не на каждый тик
        from PySide6.QtCore import QTimer
        from ydx.ui.views.photo_timeline import TILE

        tile = TILE
        if self._settings is not None:
            tile = int(self._settings.get("photo_tile") or TILE)
        self.timeline.set_tile(tile)
        self.tile_slider.blockSignals(True)
        self.tile_slider.setValue(self.timeline.tile)
        self.tile_slider.blockSignals(False)
        self._tile_timer = QTimer(self)
        self._tile_timer.setSingleShot(True)
        self._tile_timer.setInterval(120)
        self._tile_timer.timeout.connect(self._apply_tile)
        self.tile_slider.valueChanged.connect(lambda _v: self._tile_timer.start())

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

        self.timeline.open_requested.connect(self._open_photo)
        self.timeline.context_menu_requested.connect(self._context_menu)
        self.model.modelReset.connect(self._update_states)
        self.model.rowsInserted.connect(lambda *_: self._update_states())
        # таймлайн перестраивается по мере сбора (сортирует и группирует сам);
        # reset приходит и от begin_collect (пусто), и от set_items (кэш
        # сессии при возврате в раздел) — в обоих случаях отражаем модель
        self.model.rowsInserted.connect(
            lambda *_: self.timeline.set_photos(self.model.items)
        )
        self.model.modelReset.connect(
            lambda: self.timeline.set_photos(self.model.items)
        )

        # кнопка «Скачать» живёт выделением таймлайна
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

        # смена аккаунта — сбросить сессионный кэш и остановить идущий сбор
        self._accounts.active_changed.connect(lambda _a: self._invalidate())
        # куки безлимита сменились — пересобрать ленту
        if self._unlim is not None:
            self._unlim.cookies_changed.connect(lambda: self._invalidate())
            # уточнились даты съёмки — перегруппировать таймлайн
            self._unlim.dates_changed.connect(lambda: self._invalidate())

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
        # порции крупные (каждая = пересортировка таймлайна), кэш отдаёт их
        # мгновенно — вся лента из кэшей появляется за ~секунду
        collect_batch = 2000
        self._collect_id += 1
        collect_id = self._collect_id
        self._unlim_error = None
        self.model.begin_collect()
        pages = self._merged_iter(resources)

        def pull() -> list[Resource]:
            batch: list[Resource] = []
            for item in pages:                 # генератор отдаёт по одному
                batch.append(item)
                if len(batch) >= collect_batch:
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
                self._maybe_refresh_dates()

        def failed(_exc: Exception) -> None:
            if collect_id != self._collect_id:
                return
            self.model.end_collect()          # показываем то, что успели собрать
            self._update_states()
            self._update_unlim_strip()

        self._collect_next(pull, done, failed)

    def _merged_iter(self, resources):
        """Лента: сначала безлимит (свежие фото телефона — мгновенно из кэша),
        затем весь Диск (REST): кэш мгновенно, а обход дерева при изменившейся
        ревизии идёт фоном В КОНЦЕ — обновления Диска не должны прятать ленту
        безлимита на время обхода. К перезалитым фото применяем EXIF-даты из
        кэша безлимита. Ошибка безлимита не должна ронять REST-часть.
        """
        from dataclasses import replace

        overrides = self._unlim.date_overrides() if self._unlim is not None else {}

        def with_override(r):
            override = overrides.get(r.path)
            return replace(r, modified=override) if override is not None else r

        if (self._unlim is not None and self._unlim.enabled
                and self._unlim.has_cookies):
            wanted = None if self._media == "all" else self._media
            try:
                yield from self._unlim.iter_media(wanted)
            except Exception as exc:  # noqa: BLE001 — лента важнее
                self._unlim_error = exc
        yield from self._rest_iter(resources, with_override)

    def _rest_iter(self, resources, with_override):
        """REST-часть ленты: кэш мгновенно, затем проверка ревизии Диска;
        полный обход — только когда ревизия изменилась (или кэша нет)."""
        from ydx.services.photos_cache import RestFeedCache

        def wanted(r: Resource) -> bool:
            return self._media == "all" or r.media_type == self._media

        active = getattr(self._accounts, "active", None)
        login = getattr(active, "login", None) or "anon"
        cache = RestFeedCache(login)
        seen: set[str] = set()

        cached = cache.load()
        cached_rev = None
        if cached is not None:
            cached_rev, items = cached
            for r in items:
                if wanted(r) and r.path not in seen:
                    seen.add(r.path)
                    yield with_override(r)

        revision = resources.revision()
        if cached_rev == revision:
            return                    # Диск не менялся — обход не нужен

        fresh: list = []
        for r in resources.iter_media(None):     # одна ходка на фото и видео
            if r.media_type in ("image", "video"):
                fresh.append(r)
            if r.path not in seen and wanted(r):
                yield with_override(r)
        if fresh:
            cache.save(revision, fresh)

    def _maybe_refresh_dates(self) -> None:
        """Фоном уточнить даты съёмки REST-фото (батчи по 50, раз в неделю):
        готово → dates_changed → лента перегруппируется «как на сайте»."""
        if (self._unlim is None or not self._unlim.has_cookies
                or self._unlim.redates_fresh()):
            return
        stamped = [
            (r.path, r.modified) for r in self.model.items
            if not r.is_dir and r.path.startswith("disk:/")
        ]
        if not stamped:
            return
        from ydx.services.workers import run_in_thread

        run_in_thread(
            lambda: self._unlim.refresh_date_overrides(stamped),
            lambda _n: None,
            lambda _e: None,
        )

    def _collect_next(self, pull, done, failed=None) -> None:
        from ydx.services.workers import run_in_thread

        run_in_thread(pull, done, failed or (lambda _e: None))

    def _apply_tile(self) -> None:
        """Слайдер размера плиток: переложить таймлайн и запомнить выбор."""
        px = self.tile_slider.value()
        self.timeline.set_tile(px)
        self.timeline.set_photos(self.model.items)
        if self._settings is not None:
            self._settings.set("photo_tile", px)

    def _invalidate(self) -> None:
        self._collect_id += 1
        self._collected.clear()
        if self.isVisible():
            self.on_shown()

    def _set_media(self, kind: str) -> None:
        """Переключение «Фото / Видео / Все»: смена источника ленты."""
        if kind == self._media:
            return
        self._media = kind
        self.photo_btn.setChecked(kind == "image")
        self.video_btn.setChecked(kind == "video")
        self.both_btn.setChecked(kind == "all")
        self.download_btn.setEnabled(False)
        self.on_shown()

    def _refresh_badges(self) -> None:
        if self._state is None:
            return
        items = self.model.items

        def job():
            return self._state.compute(items)[0]

        def done(states) -> None:
            self.timeline.set_item_states(states)

        from ydx.services.workers import run_in_thread

        run_in_thread(job, done, lambda _e: None)

    def _update_states(self) -> None:
        if self.model.loading and not self.model.rowCount():
            self.stack.setCurrentWidget(self.loading_label)
        elif self.model.rowCount():
            self.stack.setCurrentWidget(self.timeline)
        else:
            self.stack.setCurrentWidget(self.empty_label)
        key = {"image": "photos.count", "video": "photos.count_video",
               "all": "photos.count_all"}[self._media]
        self.counter.setText(i18n.tr(key, n=self.model.rowCount()))

    # --- безлимит и скачивание ---------------------------------------------------

    def _wire_selection(self, view) -> None:
        selection = view.selectionModel()
        if selection is not None:
            selection.selectionChanged.connect(
                lambda *_: self.download_btn.setEnabled(bool(self._selected()))
            )

    def _selected(self) -> list[Resource]:
        return self.timeline.selected_resources()

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
        if self._viewer_open:
            return                     # просмотрщик открыт — новых не плодим
        items = self.model.items
        if viewer_kind(resource) == "video":
            unlim = self._unlim if resource.path.startswith("photounlim:") else None
            viewer = VideoViewer(resource, items, self, unlim=unlim)
        else:
            index = 0
            for i, r in enumerate(items):
                if r.path == resource.path:
                    index = i
                    break
            viewer = PhotoViewer(items, index, self._thumbs, self,
                                 resources_api=self._accounts.resources,
                                 unlim=self._unlim)
        self._viewer_open = True
        viewer.finished.connect(self._viewer_closed)
        viewer.exec()

    def _viewer_closed(self) -> None:
        self._viewer_open = False

    def _context_menu(self, global_pos, resources: list[Resource]) -> None:
        if not resources:
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
        act_del = QAction(i18n.tr("photos.delete"), menu)
        act_del.triggered.connect(lambda: self._delete_selected([single]))
        menu.addAction(act_del)
        menu.addSeparator()
        act_props = QAction(i18n.tr("ctx.properties"), menu)
        act_props.triggered.connect(
            lambda: PropertiesDialog(single, self._thumbs, self).exec()
        )
        menu.addAction(act_props)
        menu.exec(global_pos)

    def _delete_selected(self, resources: list[Resource]) -> None:
        """Удаление с Диска (в Корзину) — подтверждение, затем фоново."""
        from ydx.services.workers import run_in_thread

        single = resources[0]
        answer = QMessageBox.question(
            self, i18n.tr("photos.delete"),
            i18n.tr("photos.delete_confirm", name=single.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return

        if single.path.startswith("photounlim:") and self._unlim is not None:
            run_in_thread(
                lambda: self._unlim.delete_items([single]),
                lambda _ok: self._drop_items([single.path]),
                lambda exc: self._delete_failed(single, exc))
            return
        api = self._accounts.resources
        if api is None:
            return
        run_in_thread(
            lambda: api.delete(single.path),
            lambda _op: self._drop_items([single.path]),
            lambda exc: self._delete_failed(single, exc))

    def _drop_items(self, paths: list[str]) -> None:
        """Убрать удалённое из модели и собранной копии ленты."""
        drop = set(paths)
        items = [r for r in self.model.items if r.path not in drop]
        self.model.set_items(items)
        self._collected[self._media] = items

    def _delete_failed(self, resource: Resource, exc: Exception) -> None:
        QMessageBox.warning(
            self, i18n.tr("photos.delete"),
            i18n.tr("photos.delete_error",
                    name=resource.name, error=str(exc)[:120]))

    def retranslate(self) -> None:
        self.title.setText(i18n.tr("photos.title"))
        self.loading_label.setText(i18n.tr("photos.loading"))
        self.empty_label.setText(i18n.tr("photos.empty"))
        self.photo_btn.setText(i18n.tr("photos.tab_photo"))
        self.video_btn.setText(i18n.tr("photos.tab_video"))
        self.both_btn.setText(i18n.tr("photos.tab_all"))
        self.download_btn.setText(i18n.tr("ctx.download"))
        self.tile_slider.setToolTip(i18n.tr("photos.tile_size"))
        self._update_states()
