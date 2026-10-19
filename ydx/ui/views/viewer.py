"""Полноэкранный просмотр фотографий: ←/→, Esc, предзагрузка соседей."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QDialog

from ydx.core.models import Resource
from ydx.services.thumbs import ThumbService


class PhotoViewer(QDialog):
    def __init__(self, resources: list[Resource], index: int, thumbs: ThumbService, parent=None) -> None:
        super().__init__(parent)
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
