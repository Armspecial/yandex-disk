"""Встроенные просмотрщики: фото (полный экран) и видео (QMediaPlayer).

Фото показывается сразу миниатюрой, затем фоном качается оригинал с
прогрессом «скачано X из Y»; видео скачивается с тем же прогрессом и
играет сразу по готовности. Плеер создаётся лениво — только когда есть
что играть: двойной клик по файлу без ссылки не инициализирует
мультимедиа-стек вовсе.

viewer_kind() решает, чем открывать ресурс; всё остальное — через xdg-open.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QDialog

from ydx.core.models import Resource
from ydx.services.thumbs import ThumbService

log = logging.getLogger(__name__)


def viewer_kind(resource: Resource) -> str:
    """'photo' | 'video' | 'external' — чем открывать файл."""
    if not resource.is_dir:
        if resource.media_type == "image":
            return "photo"
        if resource.media_type == "video":
            return "video"
    return "external"


def _eta_text(seconds: float) -> str:
    sec = int(seconds)
    if sec < 90:
        return f"{sec} с"
    return f"{sec // 60} мин {sec % 60:02d} с"


def _progress_text(done: int, total: int, speed: float = 0.0) -> str:
    from ydx.services import i18n
    from ydx.services.fmt import human_size

    text = i18n.tr("viewer.progress",
                   done=human_size(done), total=human_size(total))
    if speed > 1:                                   # байт/с — когда уже измерена
        text += f" · {human_size(int(speed))}/с"
        if total > done:
            text += " · " + i18n.tr("viewer.left",
                                    eta=_eta_text((total - done) / speed))
    return text


class PhotoViewer(QDialog):
    """Полноэкранный просмотр фото: сразу миниатюра, фоном — оригинал."""

    _progress_updated = Signal(int, int, int)   # поколение, скачано, всего

    def __init__(self, resources: list[Resource], index: int, thumbs: ThumbService,
                 parent=None, resources_api=None, unlim=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setStyleSheet("background-color: #101012;")
        self.setCursor(Qt.CursorShape.BlankCursor)
        self._resources = resources
        self._index = index
        self._thumbs = thumbs
        self._api = resources_api
        self._unlim = unlim
        self._pixmap: QPixmap | None = None        # миниатюра (сразу)
        self._full: QPixmap | None = None          # оригинал (когда скачан)
        self._gen = 0                              # поколение загрузки (навигация)
        self._progress: tuple[int, int] | None = None
        import threading

        self._abort = threading.Event()            # закрыли окно — стоп загрузке
        from PySide6.QtWidgets import QApplication

        QApplication.instance().aboutToQuit.connect(self._abort.set)
        self._progress_updated.connect(self._set_progress)
        thumbs.ready.connect(self._on_thumb_ready)
        self._load()

    # --- загрузка ---------------------------------------------------------------

    def _current(self) -> Resource | None:
        if 0 <= self._index < len(self._resources):
            return self._resources[self._index]
        return None

    def _load(self) -> None:
        self._pixmap = None
        self._full = None
        self._progress = None
        self._gen += 1
        resource = self._current()
        if resource is None:
            self.update()
            return
        pm = self._thumbs.pixmap(resource, 1280)
        if pm is not None:
            self._pixmap = pm
        self._load_full(resource)
        self.update()
        # предзагрузка соседей
        for delta in (-1, 1):
            neighbor = self._index + delta
            if 0 <= neighbor < len(self._resources):
                self._thumbs.pixmap(self._resources[neighbor], 1280)

    def _load_full(self, resource: Resource) -> None:
        """Оригинал в кэш (если ещё нет) с прогрессом, затем показать."""
        from ydx.services import openers
        from ydx.services.workers import run_in_thread

        target = openers.cache_path(resource)
        gen = self._gen
        if target.exists():
            self._show_full(str(target), gen)
            return
        if resource.path.startswith("photounlim:") and self._unlim is not None:
            job = self._download_unlim_original(resource, target, gen)
        elif resource.path.startswith("disk:/") and self._api is not None:
            job = lambda: openers._download(   # noqa: E731 — замыкание для потока
                self._api, resource, target,
                lambda done, total: self._progress_updated.emit(gen, done, total),
                abort=self._abort)
        else:
            return
        run_in_thread(job,
                      lambda path: self._show_full(path, gen),
                      lambda _exc: None)

    def _download_unlim_original(self, resource: Resource, target, gen):
        from ydx.services import openers

        def job():
            links = self._unlim.download_links([resource])
            url = links.get(resource.path) or resource.file_url
            if not url:
                return None
            resp = self._unlim.fetch(url, stream=True)
            if resp.status_code != 200:
                return None
            return openers._stream_to_file(
                resp, target,
                lambda done, total: self._progress_updated.emit(gen, done, total),
                abort=self._abort)

        return job

    def _show_full(self, path, gen: int) -> None:
        if gen != self._gen:
            return
        pm = QPixmap(str(path))
        if not pm.isNull():
            self._full = pm
            self._progress = None
            try:
                self.update()
            except RuntimeError:
                pass                 # окно уже закрылось — ничего не рисуем

    def _set_progress(self, gen: int, done: int, total: int) -> None:
        if gen != self._gen:
            return
        self._progress = (done, total)
        self.update()

    def _on_thumb_ready(self, key: str) -> None:
        resource = self._current()
        if resource is not None and self._full is None:
            from ydx.services.thumbs import thumb_key

            if key == thumb_key(resource):
                pm = self._thumbs.pixmap(resource, 1280)
                if pm is not None:
                    self._pixmap = pm
                    self.update()

    # --- навигация ----------------------------------------------------------------

    def _step(self, delta: int) -> None:
        new_index = self._index + delta
        if 0 <= new_index < len(self._resources):
            self._index = new_index
            self._load()

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt API
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up, Qt.Key.Key_PageUp):
            self._step(-1)
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Space):
            self._step(1)
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_Back):
            self.close()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            width = self.width()
            x = event.position().x()
            if x < width * 0.3:
                self._step(-1)
            elif x > width * 0.7:
                self._step(1)
            else:
                self.close()
        elif event.button() == Qt.MouseButton.RightButton:
            self.close()
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 — Qt API
        self._step(1 if event.angleDelta().y() < 0 else -1)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
        self._abort.set()             # догрузка оригинала не будет держать выход
        super().closeEvent(event)

    # --- отрисовка ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101012"))
        source = self._full or self._pixmap
        if source is None:
            painter.setPen(QColor("#8A8D94"))
            font = QFont()
            font.setPixelSize(18)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "…")
        else:
            scaled = source.scaled(
                self.width() - 40, self.height() - 40,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        painter.setPen(QColor("#D6D7DB"))
        counter_font = QFont()
        counter_font.setPixelSize(14)
        painter.setFont(counter_font)
        resource = self._current()
        bottom = self.height() - 24
        if resource is not None:
            painter.drawText(
                20, bottom,
                f"{self._index + 1} / {len(self._resources)}   {resource.name}",
            )
        if self._progress is not None:
            painter.drawText(
                self.width() - 20, bottom, Qt.AlignmentFlag.AlignRight,
                _progress_text(*self._progress),
            )
        painter.end()


class _Speedometer:
    """Скорость загрузки (EMA по полусекундным окнам) для прогресса."""

    def __init__(self) -> None:
        self._t0: float | None = None
        self._done0 = 0
        self.speed = 0.0                     # байт/с

    def update(self, done: int) -> float:
        import time

        now = time.time()
        if self._t0 is None:
            self._t0 = now
            self._done0 = done
            return self.speed
        dt = now - self._t0
        if dt >= 0.5:
            self.speed = max(0.0, (done - self._done0) / dt)
            self._t0 = now
            self._done0 = done
        return self.speed


class VideoViewer(QDialog):
    """Видео: обычное окно, скачивание в кэш с прогрессом (скорость/остаток)
    → по готовности сразу воспроизведение.

    Стрим по прямой ссылке убран сознательно: короткоживущие ссылки рвутся
    посреди просмотра (обрывы TLS на стороне хранения), а повторные попытки
    демуксера подвешивали интерфейс. Плеер создаётся лениво — только когда
    файл скачан; клик по файлу без ссылки не трогает мультимедиа-стек вовсе.
    Управление: клик/Space — пауза, ←/→ — ±10 с, Esc — выход.
    """

    _progress_updated = Signal(int, int, float)   # скачано, всего, байт/с

    def __init__(
        self, resource: Resource, resources_api, parent=None, unlim=None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(resource.name)
        self.setStyleSheet("background-color: #101012;")
        self._resource = resource
        self._api = resources_api
        self._unlim = unlim
        self.player = None                        # лениво: см. _ensure_player
        self._played_path: str | None = None
        self._sys_btn = None
        import threading

        self._abort = threading.Event()           # закрыли окно — стоп загрузке
        from PySide6.QtWidgets import QApplication

        QApplication.instance().aboutToQuit.connect(self._abort.set)
        self._progress_updated.connect(self._set_progress)

        from PySide6.QtWidgets import QLabel, QVBoxLayout

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.status_label = QLabel()
        self.status_label.setStyleSheet("color:#D6D7DB; font-size:18px;")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.status_label, 1)

        # обычное окно ~70% экрана по центру (не фуллскрин)
        from PySide6.QtGui import QGuiApplication

        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(screen.width() * 7 // 10, screen.height() * 7 // 10)
        self.move(screen.center() - self.rect().center())

        if resource.path.startswith("photounlim:"):
            # безлимит: скачать по свежей прямой ссылке и играть из кэша
            if self._unlim is not None:
                self._start_unlim_download()
            else:
                from ydx.services import i18n

                self._set_status(i18n.tr("unlim.no_link"), error=True)
        else:
            self._start_download()

    # --- плеер (лениво) -----------------------------------------------------------

    def _ensure_player(self) -> bool:
        """QVideoWidget + QMediaPlayer + панель управления; False — мультимедиа нет."""
        if self.player is not None:
            return True
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget
            from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QWidget
        except Exception:   # noqa: BLE001 — нет мультимедиа → системный плеер
            self._fatal_fallback()
            return False

        self.status_label.hide()
        self.video_widget = QVideoWidget()
        self.layout().insertWidget(0, self.video_widget, 1)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setVideoOutput(self.video_widget)
        self.player.setAudioOutput(self.audio)
        self.player.errorOccurred.connect(
            lambda _e, msg, _s="": self._on_error(msg))
        self.player.mediaStatusChanged.connect(
            lambda status: log.info("video media status: %s", status))

        bar = QWidget(self)
        bar.setStyleSheet("background-color: rgba(16,16,18,0.85);")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(10)
        self.play_btn = QPushButton("⏸")
        self.play_btn.setFixedSize(36, 36)
        self.play_btn.clicked.connect(self._toggle_play)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.player.durationChanged.connect(self._on_duration)
        self.player.positionChanged.connect(self._on_position)
        self.slider.sliderMoved.connect(lambda ms: self.player.setPosition(ms))
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setStyleSheet("color:#D6D7DB;")
        self.error_label = QLabel()
        self.error_label.setStyleSheet("color:#FF7A5C;")
        self.error_label.hide()
        row.addWidget(self.play_btn)
        row.addWidget(self.slider, 1)
        row.addWidget(self.time_label)
        row.addWidget(self.error_label)
        self.layout().addWidget(bar)
        return True

    # --- загрузка и воспроизведение --------------------------------------------

    def _start_unlim_download(self) -> None:
        """Скачать видео безлимита по свежей прямой ссылке и проиграть."""
        from ydx.constants import OPEN_CACHE_DIR
        from ydx.services import i18n, openers
        from ydx.services.workers import run_in_thread

        self._set_status(i18n.tr("viewer.loading_video"))
        speedometer = _Speedometer()

        def job() -> dict:
            return openers.unlim_download(
                self._unlim, [self._resource], OPEN_CACHE_DIR,
                progress=lambda _path, done, total: self._progress_updated.emit(
                    done, total, speedometer.update(done)),
                abort=self._abort)

        def done(result: dict) -> None:
            if result["paths"]:
                self._play_file(result["paths"][0])
            else:
                error = (result.get("errors") or {}).get(self._resource.path)
                self._on_error(error or i18n.tr("unlim.no_link"))

        run_in_thread(job, done, lambda exc: self._on_error(str(exc)))

    def _start_download(self) -> None:
        from ydx.services import i18n, openers
        from ydx.services.workers import run_in_thread

        self._set_status(i18n.tr("viewer.loading_video"))
        speedometer = _Speedometer()

        def job():
            return openers._download(
                self._api, self._resource, openers.cache_path(self._resource),
                lambda done, total: self._progress_updated.emit(
                    done, total, speedometer.update(done)),
                abort=self._abort)

        run_in_thread(
            job,
            self._play_file,
            lambda exc: self._on_error(str(exc)),
        )

    def _play_file(self, path) -> None:
        if not self.isVisible() or not self._ensure_player():
            return
        from PySide6.QtCore import QUrl

        log.info("video play: %s", path)
        self.status_label.hide()
        self._played_path = path
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
        from PySide6.QtCore import QTimer

        QTimer.singleShot(3000, self._kick_if_stuck)

    def _kick_if_stuck(self) -> None:
        """Через 3 с после старта: если не играет и без ошибки — толкаем ещё
        раз и пишем состояние в лог (диагностика «не открывается»)."""
        if self.player is None or not self.isVisible():
            return
        state = self.player.playbackState()
        if state != self.player.PlaybackState.PlayingState:
            log.warning("video stuck after start: state=%s pos=%dms status=%s",
                        state, self.player.position(),
                        self.player.mediaStatus())
            self.player.play()

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            "color:#FF7A5C; font-size:18px;" if error else "color:#D6D7DB; font-size:18px;")
        if self.player is None or error:
            self.status_label.show()          # ошибка — крупно, поверх видео
        if error and self.player is not None:
            self.error_label.setText(text)
            self.error_label.show()

    def _set_progress(self, done: int, total: int, speed: float = 0.0) -> None:
        self._set_status(_progress_text(done, total, speed))

    def _on_duration(self, ms: int) -> None:
        self.slider.setRange(0, max(ms, 1))

    def _on_position(self, ms: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(ms)
        self.slider.blockSignals(False)
        self.time_label.setText(f"{self._fmt(ms)} / {self._fmt(self.player.duration())}")

    @staticmethod
    def _fmt(ms: int) -> str:
        sec = int(ms / 1000)
        return f"{sec // 60}:{sec % 60:02d}"

    def _toggle_play(self) -> None:
        if self.player is None:
            return
        if self.player.playbackState() == self.player.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText("▶")
        else:
            self.player.play()
            self.play_btn.setText("⏸")

    def _on_error(self, message: str) -> None:
        from ydx.services import i18n

        log.warning("video error: %s", message)
        self._set_status(i18n.tr("viewer.video_error") + f" ({message[:80]})",
                         error=True)
        self._offer_system_player()

    def _offer_system_player(self) -> None:
        """Встроенный не справился, но файл скачан — откроем системным плеером."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QPushButton

        if getattr(self, "_sys_btn", None) is not None:
            return
        path = getattr(self, "_played_path", None)
        if path is None:
            from ydx.services import openers

            cached = openers.cache_path(self._resource)
            if cached.exists():
                path = str(cached)
        if path is None:
            return
        from ydx.services import i18n

        self._sys_btn = QPushButton(i18n.tr("viewer.open_external"))
        self._sys_btn.setMinimumHeight(36)
        self._sys_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(path)))
        self.layout().addWidget(self._sys_btn)

    def _fatal_fallback(self) -> None:
        """Мультимедиа недоступна — открываем системным плеером и закрываемся."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from ydx.services import openers

        if self._resource.path.startswith("photounlim:"):
            cached = openers.cache_path(self._resource)
            if cached.exists():                     # уже скачано — отдадим системе
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(cached)))
            elif self._resource.file_url:
                QDesktopServices.openUrl(QUrl(self._resource.file_url))
            self.close()
            return
        openers.open_resource(self.parent(), self._api, self._resource)
        self.close()

    # --- управление ------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt API
        key = event.key()
        if key in (Qt.Key.Key_Space, ):
            self._toggle_play()
        elif key == Qt.Key.Key_Left and self.player is not None:
            self.player.setPosition(max(self.player.position() - 10_000, 0))
        elif key == Qt.Key.Key_Right and self.player is not None:
            self.player.setPosition(self.player.position() + 10_000)
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_Back):
            self.close()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            if self.player is None:
                self.close()         # показывать нечего — клик закрывает
            else:
                self._toggle_play()
        elif event.button() == Qt.MouseButton.RightButton:
            self.close()
        super().mousePressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
        self._abort.set()             # зависшая загрузка не будет держать выход
        super().closeEvent(event)
