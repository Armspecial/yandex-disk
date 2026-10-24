"""Встроенные просмотрщики: фото (полный экран) и видео (QMediaPlayer).

viewer_kind() решает, чем открывать ресурс; всё остальное — через xdg-open.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QDialog

from ydx.core.models import Resource
from ydx.services.thumbs import ThumbService


def viewer_kind(resource: Resource) -> str:
    """'photo' | 'video' | 'external' — чем открывать файл."""
    if not resource.is_dir:
        if resource.media_type == "image":
            return "photo"
        if resource.media_type == "video":
            return "video"
    return "external"


class PhotoViewer(QDialog):
    def __init__(self, resources: list[Resource], index: int, thumbs: ThumbService, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setStyleSheet("background-color: #101012;")
        self.setCursor(Qt.CursorShape.BlankCursor)
        self._resources = resources
        self._index = index
        self._thumbs = thumbs
        self._pixmap: QPixmap | None = None

        thumbs.ready.connect(self._on_thumb_ready)
        self._load()

    # --- загрузка ---------------------------------------------------------------

    def _current(self) -> Resource | None:
        if 0 <= self._index < len(self._resources):
            return self._resources[self._index]
        return None

    def _load(self) -> None:
        self._pixmap = None
        resource = self._current()
        if resource is None:
            self.update()
            return
        pm = self._thumbs.pixmap(resource, 1280)
        if pm is not None:
            self._pixmap = pm
            self.update()
        # предзагрузка соседей
        for delta in (-1, 1):
            neighbor = self._index + delta
            if 0 <= neighbor < len(self._resources):
                self._thumbs.pixmap(self._resources[neighbor], 1280)

    def _on_thumb_ready(self, key: str) -> None:
        resource = self._current()
        if resource is not None:
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

    # --- отрисовка ------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 — Qt API
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#101012"))
        if self._pixmap is None:
            painter.setPen(QColor("#8A8D94"))
            font = QFont()
            font.setPixelSize(18)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "…")
            painter.end()
            return
        scaled = self._pixmap.scaled(
            self.width() - 40, self.height() - 40,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
        resource = self._current()
        if resource is not None:
            painter.setPen(QColor("#D6D7DB"))
            counter_font = QFont()
            counter_font.setPixelSize(14)
            painter.setFont(counter_font)
            painter.drawText(
                20, self.height() - 24,
                f"{self._index + 1} / {len(self._resources)}   {resource.name}",
            )
        painter.end()


class VideoViewer(QDialog):
    """Видео прямо в приложении: скачивание в кэш → QMediaPlayer.

    Управление: клик/Space — пауза, ←/→ — ±10 с, Esc — выход.
    """

    def __init__(
        self, resource: Resource, resources_api, parent=None, unlim=None,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setStyleSheet("background-color: #101012;")
        self._resource = resource
        self._api = resources_api
        self._unlim = unlim
        self._stream_url: str | None = None      # играем по прямой ссылке (безлимит)
        self._stream_fallback_done = False

        from PySide6.QtWidgets import QVBoxLayout, QLabel, QHBoxLayout, QPushButton, QSlider

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self.video_widget = None
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            from PySide6.QtMultimediaWidgets import QVideoWidget

            self.video_widget = QVideoWidget()
            col.addWidget(self.video_widget, 1)
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setVideoOutput(self.video_widget)
            self.player.setAudioOutput(self.audio)
            self.player.errorOccurred.connect(lambda _e, msg, _s="": self._on_error(msg))
        except Exception:   # noqa: BLE001 — нет мультимедиа → откажемся в конструкторе
            self._fatal_fallback()
            return

        # нижняя панель управления
        from PySide6.QtWidgets import QWidget

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
        col.addWidget(bar)

        self.loading_label = QLabel()
        self.loading_label.setStyleSheet("color:#D6D7DB; font-size:16px;")
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setGeometry(0, 0, 10, 10)
        self.loading_label.setParent(self)

        if resource.path.startswith("photounlim:"):
            # безлимит: стрим по прямой ссылке, фолбэк — скачать кукисессией
            from ydx.services import i18n

            if resource.file_url:
                self._play_url(resource.file_url)
            else:
                self._on_error(i18n.tr("unlim.no_link"))
        else:
            self._start_download()

    # --- загрузка и воспроизведение --------------------------------------------

    def _play_url(self, url: str) -> None:
        """Воспроизвести по прямой ссылке (безлимитное видео)."""
        from PySide6.QtCore import QUrl

        self._stream_url = url
        self.player.setSource(QUrl(url))
        self.player.play()

    def _start_unlim_download(self) -> None:
        """Стрим не пошёл — скачать безлимитное видео в кэш и проиграть."""
        from ydx.constants import OPEN_CACHE_DIR
        from ydx.services import i18n, openers
        from ydx.services.workers import run_in_thread

        self._show_loading(True)

        def job() -> dict:
            return openers.unlim_download(self._unlim, [self._resource], OPEN_CACHE_DIR)

        def done(result: dict) -> None:
            if result["done"]:
                self._play_file(openers.cache_path(self._resource))
            else:
                self._on_error(i18n.tr("unlim.no_link"))

        run_in_thread(job, done, lambda exc: self._on_error(str(exc)))

    def _start_download(self) -> None:
        from ydx.services import openers
        from ydx.services.workers import run_in_thread

        self._show_loading(True)

        def job():
            return openers._download(self._api, self._resource, openers.cache_path(self._resource))

        run_in_thread(
            job,
            self._play_file,
            lambda exc: self._on_error(str(exc)),
        )

    def _play_file(self, path) -> None:
        if not self.isVisible():
            return
        self._show_loading(False)
        from PySide6.QtCore import QUrl

        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()

    def _show_loading(self, on: bool) -> None:
        from ydx.services import i18n

        if on:
            self.loading_label.setText(i18n.tr("viewer.loading_video"))
            self.loading_label.adjustSize()
            self.loading_label.move(
                (self.width() - self.loading_label.width()) // 2,
                (self.height() - self.loading_label.height()) // 2,
            )
        self.loading_label.setVisible(on)

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
        if self.player.playbackState() == self.player.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText("▶")
        else:
            self.player.play()
            self.play_btn.setText("⏸")

    def _on_error(self, message: str) -> None:
        from ydx.services import i18n

        if (self._stream_url is not None and not self._stream_fallback_done
                and self._unlim is not None):
            self._stream_fallback_done = True
            self._start_unlim_download()      # стрим не пошёл — скачаем кукисессией
            return
        self._show_loading(False)
        self.error_label.setText(i18n.tr("viewer.video_error") + f" ({message[:80]})")
        self.error_label.show()

    def _fatal_fallback(self) -> None:
        """Мультимедиа недоступна — открываем системным плеером и закрываемся."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from ydx.services import openers

        if self._resource.path.startswith("photounlim:"):
            if self._resource.file_url:
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
        elif key == Qt.Key.Key_Left:
            self.player.setPosition(max(self.player.position() - 10_000, 0))
        elif key == Qt.Key.Key_Right:
            self.player.setPosition(self.player.position() + 10_000)
        elif key in (Qt.Key.Key_Escape, Qt.Key.Key_Back):
            self.close()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_play()
        elif event.button() == Qt.MouseButton.RightButton:
            self.close()
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt API
        if self.loading_label.isVisible():
            self._show_loading(True)
        super().resizeEvent(event)
