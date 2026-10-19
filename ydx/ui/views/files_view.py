"""Представление файлов: QListView + делегат, рисующий список и сетку.

Превью подгружаются сервисом ThumbService по требованию закрашиваемых ячеек
(видимые — первыми), готовность — через thumbs.ready → viewport().update().
"""

from __future__ import annotations

from PySide6.QtCore import QMimeData, QModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QListView, QStyledItemDelegate, QStyle

from ydx.core.models import Resource
from ydx.services import mime
from ydx.services.fmt import human_date, human_size
from ydx.services.thumbs import ThumbService
from ydx.ui.theme import theme
from ydx.ui.views.files_model import MIME_INTERNAL, ResourceRole

LIST_ROW = 42
GRID_W, GRID_H = 128, 152
THUMB_GRID = 96
THUMB_LIST = 28


def _sel_colors() -> tuple[QColor, QColor]:
    """(фон выбора, фон ховера) под текущую тему."""
    if theme().current == "dark":
        return QColor(255, 204, 0, 52), QColor(255, 255, 255, 18)
    return QColor(255, 204, 0, 44), QColor(0, 0, 0, 14)


class FilesDelegate(QStyledItemDelegate):
    """Значки состояний: 'offline' (скачан, синяя стрелка), 'synced'
    (синхронизирован демоном, зелёная галочка), 'cloud' (только в облаке)."""

    def __init__(self, thumbs: ThumbService, parent=None) -> None:
        super().__init__(parent)
        self.thumbs = thumbs
        self.mode = "list"
        self.item_states: dict[str, str] = {}

    # --- размеры ------------------------------------------------------------------

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 — Qt API
        if self.mode == "grid":
            return QSize(GRID_W, GRID_H)
        return QSize(0, LIST_ROW)

    # --- отрисовка ------------------------------------------------------------------

    def paint(self, painter: QPainter, option, index) -> None:
        resource: Resource | None = index.data(ResourceRole)
        if resource is None:
            return
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = option.rect
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
            if selected or hovered:
                sel, hov = _sel_colors()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(sel if selected else hov)
                painter.drawRoundedRect(rect.adjusted(2, 2, -2, -2), 8, 8)

            if self.mode == "grid":
                self._paint_grid(painter, rect, resource, index)
            else:
                self._paint_list(painter, rect, resource, index)
        finally:
            painter.restore()

    def _paint_list(self, p: QPainter, rect: QRect, r: Resource, index: QModelIndex) -> None:
        icon_rect = QRect(rect.left() + 14, rect.top() + (rect.height() - THUMB_LIST) // 2,
                          THUMB_LIST, THUMB_LIST)
        pm = self._icon(r, THUMB_LIST, index)
        p.drawPixmap(icon_rect, pm)
        self._sync_badge(p, icon_rect, r)
        name_x = icon_rect.right() + 14
        date_w, size_w = 150, 90
        name_w = rect.right() - name_x - date_w - size_w - 40
        if name_w > 40:
            metrics = QFontMetrics(p.font())
            name = metrics.elidedText(r.name, Qt.TextElideMode.ElideRight, name_w)
            if r.is_dir:
                font = QFont(p.font())
                font.setWeight(QFont.Weight.DemiBold)
                p.setFont(font)
            p.setPen(QColor("#F2F2F4" if theme().current == "dark" else "#21201F"))
            p.drawText(
                QRect(name_x, rect.top(), name_w, rect.height()),
                Qt.AlignmentFlag.AlignVCenter, name,
            )
        text_color = QColor("#A3A5AC")
        p.setPen(text_color)
        date_rect = QRect(rect.right() - date_w - size_w - 16, rect.top(), date_w, rect.height())
        p.drawText(date_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   human_date(r.modified))
        size_rect = QRect(rect.right() - size_w - 12, rect.top(), size_w, rect.height())
        p.drawText(size_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                   "" if r.is_dir else human_size(r.size))
        if r.public_url:
            self._badge(p, icon_rect)

    def _paint_grid(self, p: QPainter, rect: QRect, r: Resource, index: QModelIndex) -> None:
        pm = self._icon(r, THUMB_GRID, index)
        icon_rect = QRect(
            rect.left() + (rect.width() - THUMB_GRID) // 2,
            rect.top() + 8, THUMB_GRID, THUMB_GRID,
        )
        p.drawPixmap(icon_rect, pm)
        self._sync_badge(p, icon_rect, r)
        if r.public_url:
            self._badge(p, icon_rect)
        text_top = icon_rect.bottom() + 6
        text_rect = QRect(rect.left() + 6, text_top, rect.width() - 12, rect.height() - text_top - 8)
        metrics = QFontMetrics(p.font())
        name = metrics.elidedText(r.name, Qt.TextElideMode.ElideMiddle, text_rect.width() * 2)
        p.setPen(QColor("#F2F2F4" if theme().current == "dark" else "#21201F"))
        p.drawText(QRect(text_rect.left(), text_top, text_rect.width(), 34),
                   Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap, name)
        if not r.is_dir:
            p.setPen(QColor("#A3A5AC"))
            p.drawText(QRect(text_rect.left(), text_top + 36, text_rect.width(), 16),
                       Qt.AlignmentFlag.AlignHCenter, human_size(r.size))

    def _icon(self, r: Resource, size: int, index: QModelIndex):
        if not r.is_dir and r.media_type == "image" and r.preview:
            pm = self.thumbs.pixmap(r, size)
            if pm is not None:
                return pm
        return mime.resource_pixmap(r, size)

    def _sync_badge(self, p: QPainter, icon_rect: QRect, r: Resource) -> None:
        """Значок состояния файла относительно компьютера.

        offline — синий диск со стрелкой «скачан на компьютер»;
        synced  — зелёный диск с галочкой (синхронизирован демоном);
        cloud   — тёмно-серый диск с облаком (только онлайн).
        """
        state = self.item_states.get(r.path)
        if not state:
            return
        d = 15
        x, y = icon_rect.left() - 4, icon_rect.bottom() - d
        cx, cy = x + d / 2, y + d / 2
        p.setPen(Qt.PenStyle.NoPen)
        white = QPen(QColor("#FFFFFF"), 1.7)
        white.setCapStyle(Qt.PenCapStyle.RoundCap)
        white.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if state == "offline":
            p.setBrush(QColor("#2E6BE6"))
            p.drawEllipse(x, y, d, d)
            p.setPen(white)
            ix, iy = int(cx), int(cy)
            p.drawLine(ix, iy - 4, ix, iy + 1)                    # стрелка вниз
            p.drawLine(ix - 2, iy - 1, ix, iy + 1)
            p.drawLine(ix + 2, iy - 1, ix, iy + 1)
            p.drawLine(int(cx) - 3, iy + 4, int(cx) + 3, iy + 4)  # «лоток» компьютера
        elif state == "synced":
            p.setBrush(QColor("#1DA740"))
            p.drawEllipse(x, y, d, d)
            p.setPen(white)
            p.drawLine(int(cx) - 4, int(cy), int(cx) - 1, int(cy) + 3)
            p.drawLine(int(cx) - 1, int(cy) + 3, int(cx) + 4, int(cy) - 3)
        elif state == "cloud":
            p.setBrush(QColor("#5F6368"))
            p.drawEllipse(x, y, d, d)
            p.setPen(white)
            x0, y0 = int(cx) - 4, int(cy) + 1
            p.drawArc(x0, y0 - 2, 5, 5, 0, 180 * 16)              # левый бугор
            p.drawArc(x0 + 2, y0 - 4, 5, 6, 20 * 16, 180 * 16)    # правый бугор
            p.drawLine(x0, y0, x0 + 6, y0)                        # основание

    def _badge(self, p: QPainter, icon_rect: QRect) -> None:
        """Значок «опубликовано» — маленькая точка-ссылка в углу иконки."""
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2E6BE6"))
        d = 10
        p.drawEllipse(icon_rect.right() - d, icon_rect.bottom() - d, d, d)
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        cx, cy = icon_rect.right() - d // 2, icon_rect.bottom() - d // 2
        p.drawEllipse(QRect(cx - 3, cy - 1, 6, 3))


class FilesView(QListView):
    open_requested = Signal(object)              # Resource
    context_menu_requested = Signal(QPoint, list)  # глобальная позиция, [Resource]
    move_requested = Signal(list, str)           # [path], целевая папка
    files_dropped = Signal(list)                 # [QUrl] локальные файлы (загрузка)

    def __init__(self, model, thumbs: ThumbService, parent=None) -> None:
        super().__init__(parent)
        self.setModel(model)
        self._delegate = FilesDelegate(thumbs, self)
        self.setItemDelegate(self._delegate)
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._emit_context)
        self.doubleClicked.connect(self._on_double_click)
        self.setDragEnabled(True)
        self.setDragDropMode(QListView.DragDropMode.DragDrop)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.viewport().setAcceptDrops(True)
        self.setEditTriggers(QListView.EditTrigger.NoEditTriggers)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.set_vertical_scroll_mode()
        thumbs.ready.connect(lambda _k: self.viewport().update())
        self.set_mode("list")

    # --- режимы ---------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        self._delegate.mode = mode
        if mode == "grid":
            self.setViewMode(QListView.ViewMode.IconMode)
            self.setGridSize(QSize(GRID_W + 16, GRID_H + 12))
            self.setSpacing(4)
            self.setWordWrap(False)
        else:
            self.setViewMode(QListView.ViewMode.ListMode)
            self.setGridSize(QSize())
            self.setSpacing(0)
        self.reset()

    def set_item_states(self, states: dict[str, str]) -> None:
        self._delegate.item_states = states
        self.viewport().update()

    def set_vertical_scroll_mode(self) -> None:
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QListView.ScrollMode.ScrollPerPixel)

    # --- события ----------------------------------------------------------------

    def selected_resources(self) -> list[Resource]:
        return [
            ix.data(ResourceRole) for ix in self.selectedIndexes() if ix.data(ResourceRole)
        ]

    def _emit_context(self, pos: QPoint) -> None:
        global_pos = self.viewport().mapToGlobal(pos)
        selected = self.selected_resources()
        index = self.indexAt(pos)
        if not selected and index.isValid():
            res = index.data(ResourceRole)
            if res:
                selected = [res]
        self.context_menu_requested.emit(global_pos, selected)

    def _on_double_click(self, index: QModelIndex) -> None:
        res = index.data(ResourceRole)
        if res is not None:
            self.open_requested.emit(res)

    def keyPressEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            sel = self.selected_resources()
            if len(sel) == 1:
                self.open_requested.emit(sel[0])
                return
        super().keyPressEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.mimeData().hasUrls() or event.mimeData().hasFormat(MIME_INTERNAL):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 — Qt API
        if event.mimeData().hasUrls() or event.mimeData().hasFormat(MIME_INTERNAL):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 — Qt API
        mime_data: QMimeData = event.mimeData()
        pos = event.position().toPoint()
        index = self.indexAt(pos)
        target = index.data(ResourceRole) if index.isValid() else None

        if mime_data.hasFormat(MIME_INTERNAL) and target is not None and target.is_dir:
            paths = bytes(mime_data.data(MIME_INTERNAL)).decode("utf-8").splitlines()
            if target.path not in paths:
                self.move_requested.emit(paths, target.path)
                event.acceptProposedAction()
                return
        if mime_data.hasUrls():
            self.files_dropped.emit(mime_data.urls())
            event.acceptProposedAction()
            return
        event.ignore()
