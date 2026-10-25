"""Таймлайн фото: сетка, сгруппированная по дням, с заголовками-датами.

QTableView со span-строками: заголовок дня («Сегодня», «7 октября 2025»)
занимает строку целиком, за ним строки миниатюр этого дня. Вид только
раскладывает по сетке — сортирует новые-вперёд сам (по modified).
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import (
    QAbstractTableModel,
    QLocale,
    QModelIndex,
    QPointF,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPolygonF
from PySide6.QtWidgets import QHeaderView, QStyle, QTableView

from ydx.core.models import Resource
from ydx.services import i18n
from ydx.services.thumbs import ThumbService
from ydx.ui.views.files_model import ResourceRole
from ydx.ui.views.files_view import FilesDelegate, _sel_colors

TILE = 176                # ячейка миниатюры (ширина колонки и высота строки)
TILE_MIN = 96             # границы регулировки размера плиток
TILE_MAX = 288
THUMB_MARGIN = 24         # поля внутри ячейки (миниатюра = плитка − margin)
HEADER_H = 44             # высота строки-заголовка дня

HeaderRole = Qt.ItemDataRole.UserRole + 10   # заголовок дня (строка)


def day_title(day, today) -> str:
    """«Сегодня», «Вчера», «7 октября», «7 октября 2025» (год — если не текущий)."""
    if day == today:
        return i18n.tr("photos.today")
    if (today - day).days == 1:
        return i18n.tr("photos.yesterday")
    locale = QLocale(i18n.current_language())
    text = locale.toString(day, "d MMMM")
    if day.year != today.year:
        text += f" {day.year}"
    return text


class PhotoTimelineModel(QAbstractTableModel):
    """Строки сетки: заголовок дня (одна на день) + строки фото этого дня."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._photos: list[Resource] = []
        self._rows: list[dict] = []      # {'header': str} | {'photos': [Resource]}
        self._cols = 4

    # --- наполнение -------------------------------------------------------------

    def set_photos(self, photos: list[Resource]) -> None:
        """Полная пересборка: сортировка новые-вперёд и группировка по дням."""
        self.beginResetModel()
        self._photos = sorted(
            photos,
            key=lambda r: r.modified.timestamp() if r.modified else 0,
            reverse=True,
        )
        self._rebuild()
        self.endResetModel()

    def set_columns(self, cols: int) -> None:
        cols = max(1, cols)
        if cols == self._cols:
            return
        self.beginResetModel()
        self._cols = cols
        self._rebuild()
        self.endResetModel()

    def _rebuild(self) -> None:
        grouped: list[dict] = []
        today = datetime.now().date()
        current_day = object()          # сентинел: гарантированно != первой даты
        for r in self._photos:
            day = r.modified.astimezone().date() if r.modified else None
            if day != current_day:
                current_day = day
                grouped.append(
                    {"header": day_title(day, today) if day else i18n.tr("photos.no_date")}
                )
                grouped.append({"photos": []})
            grouped[-1]["photos"].append(r)

        # фото дня → строки по self._cols (иначе хвост дня не виден в сетке)
        self._rows = []
        for row in grouped:
            if "header" in row:
                self._rows.append(row)
            else:
                for i in range(0, len(row["photos"]), self._cols):
                    self._rows.append({"photos": row["photos"][i:i + self._cols]})

    # --- модель -------------------------------------------------------------------

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 0 if parent and parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 0 if parent and parent.isValid() else self._cols

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        if "header" in row:
            return row["header"] if role == HeaderRole else None
        photos = row["photos"]
        if not (0 <= index.column() < len(photos)):
            return None
        if role == ResourceRole:
            return photos[index.column()]
        return None

    # --- удобное -------------------------------------------------------------------

    def resource_at(self, index: QModelIndex) -> Resource | None:
        return index.data(ResourceRole)


class _TileDelegate(FilesDelegate):
    """Ячейка-миниатюра без подписей; для видео — постер, если есть.
    Размер плитки спрашивает у вьюхи — он регулируется слайдером."""

    def __init__(self, thumbs: ThumbService, view: "PhotoTimelineView") -> None:
        super().__init__(thumbs)
        self._view = view

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 — Qt API
        return QSize(self._view.tile, self._view.tile)

    def paint(self, painter, option, index) -> None:
        resource = index.data(ResourceRole)
        painter.save()
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = option.rect
            if resource is None:                      # строка-заголовок дня
                self._paint_header(painter, rect, index)
                return
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
            if selected or hovered:
                sel, hov = _sel_colors()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(sel if selected else hov)
                painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 10, 10)
            thumb = self._view.tile - THUMB_MARGIN
            pm = self._icon(resource, thumb, index)
            icon_rect = QRect(
                rect.left() + (rect.width() - thumb) // 2,
                rect.top() + (rect.height() - thumb) // 2,
                thumb, thumb,
            )
            painter.drawPixmap(icon_rect, pm)
            self._sync_badge(painter, icon_rect, resource)
            if resource.media_type == "video":
                self._paint_video_badge(painter, rect)
        finally:
            painter.restore()

    @staticmethod
    def _paint_video_badge(painter: QPainter, rect: QRect) -> None:
        """Бейдж «▶» в правом верхнем углу плитки: видео отличается от фото."""
        w, h = 34, 24
        x = rect.right() - w - 8
        y = rect.top() + 8
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawRoundedRect(x, y, w, h, 6, 6)
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.drawPolygon(QPolygonF([
            QPointF(x + 13, y + 6), QPointF(x + 13, y + h - 6),
            QPointF(x + 24, y + h / 2),
        ]))

    def _paint_header(self, painter, rect: QRect, index: QModelIndex) -> None:
        title = index.data(HeaderRole) or ""
        painter.setPen(QColor("#F2F2F4" if _dark() else "#21201F"))
        font = QFont(painter.font())
        font.setPointSizeF(11.5)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(
            QRect(rect.left() + 14, rect.top(), rect.width() - 20, rect.height()),
            Qt.AlignmentFlag.AlignVCenter, title,
        )

    def _icon(self, r: Resource, size: int, index: QModelIndex):
        """Как FilesDelegate._icon, но превью берём и у видео (постер)."""
        if not r.is_dir and r.preview:
            pm = self.thumbs.pixmap(r, size)
            if pm is not None:
                return pm
        from ydx.services import mime

        return mime.resource_pixmap(r, size)


def _dark() -> bool:
    from ydx.ui.theme import theme

    return theme().current == "dark"


class PhotoTimelineView(QTableView):
    """Сетка «дни + миниатюры»: заголовки на всю ширину, тайлы ниже."""

    open_requested = Signal(object)                # Resource
    context_menu_requested = Signal(object, list)  # глобальная позиция, [Resource]

    def __init__(self, thumbs: ThumbService, parent=None) -> None:
        super().__init__(parent)
        self.tile = TILE                 # размер плитки (регулируется слайдером)
        self.timeline_model = PhotoTimelineModel(self)
        self.setModel(self.timeline_model)
        self._delegate = _TileDelegate(thumbs, self)
        self.setItemDelegate(self._delegate)

        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectItems)
        self.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.setCornerButtonEnabled(False)
        self.setWordWrap(False)
        self.horizontalHeader().hide()
        self.horizontalHeader().setDefaultSectionSize(self.tile)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(self.tile)
        self.setVerticalScrollMode(QTableView.ScrollMode.ScrollPerPixel)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self.doubleClicked.connect(self._on_double_click)
        self.customContextMenuRequested.connect(self._emit_context)
        self.timeline_model.modelReset.connect(self._apply_layout)
        thumbs.ready.connect(lambda _k: self.viewport().update())

    # --- наполнение ---------------------------------------------------------------

    def set_photos(self, photos: list[Resource]) -> None:
        self.timeline_model.set_photos(photos)

    def set_tile(self, px: int) -> None:
        """Новый размер плитки: границы, ширина колонок и их число; полную
        раскладку (высоты строк) доделает следующий set_photos/reset."""
        px = max(TILE_MIN, min(TILE_MAX, int(px)))
        if px == self.tile:
            return
        self.tile = px
        self.horizontalHeader().setDefaultSectionSize(px)
        self.verticalHeader().setDefaultSectionSize(px)
        self.timeline_model.set_columns(max(1, self.viewport().width() // px))

    def set_item_states(self, states: dict[str, str]) -> None:
        self._delegate.item_states = states
        self.viewport().update()

    # --- раскладка ----------------------------------------------------------------

    def _apply_layout(self) -> None:
        """Span-строки заголовков + высоты строк (после каждой пересборки)."""
        model = self.timeline_model
        # спаны прошлой раскладки живут во вьюхе и не снимаются сами: после
        # сдвига строк (порция новее / ресайз) они накрывают ячейки фото,
        # и день «теряет» часть снимков — сбрасываем перед новой раскладкой
        self.clearSpans()
        for row in range(model.rowCount()):
            if model.index(row, 0).data(ResourceRole) is None:
                self.setSpan(row, 0, 1, model.columnCount())
                self.setRowHeight(row, HEADER_H)
            else:
                self.setRowHeight(row, self.tile)

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt API
        super().resizeEvent(event)
        cols = max(1, self.viewport().width() // self.tile)
        self.timeline_model.set_columns(cols)

    # --- события --------------------------------------------------------------------

    def selected_resources(self) -> list[Resource]:
        return [
            ix.data(ResourceRole) for ix in self.selectedIndexes() if ix.data(ResourceRole)
        ]

    def _emit_context(self, pos) -> None:
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
