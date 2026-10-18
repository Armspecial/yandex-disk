"""Тулбар страницы файлов: назад, хлебные крошки, поиск, вид, сортировка."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from ydx.services import i18n
from ydx.ui.widgets import icon

SORT_VALUES = [("modified", "files.sort.modified"), ("name", "files.sort.name"),
               ("size", "files.sort.size")]


class Breadcrumbs(QWidget):
    path_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("breadcrumbs")
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._segments: list[tuple[str, str]] = []   # (имя, путь)
        self._path = "disk:/"
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    @property
    def path(self) -> str:
        return self._path

    def set_path(self, path: str) -> None:
        self._path = path
        parts = [p for p in path.split("/") if p]
        # path вида disk:/a/b → ['a','b']
        self._segments = [(i18n.tr("props.disk_root"), "disk:/")]
        acc = "disk:/"
        for name in parts:
            acc = (acc.rstrip("/") ) + "/" + name
            self._segments.append((name, acc))
        self._rebuild()

    def _rebuild(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        max_width = self.width()
        shown = self._segments
        ellided = False
        if max_width > 0:
            # если сегменты не влезают — сворачиваем середину в «…»
            approx = sum(len(n) * 8 + 26 for n, _ in self._segments)
            if approx > max_width and len(self._segments) > 2:
                shown = [self._segments[0], ("…", ""), self._segments[-1]]
                ellided = True
        for i, (name, seg_path) in enumerate(shown):
            btn = QPushButton(name)
            btn.setObjectName("crumb")
            last = i == len(shown) - 1
            if last:
                btn.setObjectName("crumbLast")
            btn.setCursor(Qt.CursorShape.PointingHandCursor if (seg_path and not last) else Qt.CursorShape.ArrowCursor)
            if seg_path and not last:
                btn.clicked.connect(lambda _=False, p=seg_path: self.path_changed.emit(p))
            elif ellided and name == "…":
                btn.setObjectName("crumb")
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(self._show_hidden_menu)
            self._layout.addWidget(btn)
            if not last:
                sep = QPushButton("›")
                sep.setObjectName("crumb")
                sep.setEnabled(False)
                sep.setFixedWidth(14)
                self._layout.addWidget(sep)
        self._layout.addStretch(1)

    def _show_hidden_menu(self) -> None:
        from PySide6.QtGui import QAction
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        for name, seg_path in self._segments:
            action = QAction(name, menu)
            action.triggered.connect(lambda _=False, p=seg_path: self.path_changed.emit(p))
            menu.addAction(action)
        btn = self.sender()
        if isinstance(btn, QWidget):
            menu.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt API
        super().resizeEvent(event)
        self._rebuild()


class FilesToolbar(QWidget):
    back_clicked = Signal()
    search_changed = Signal(str)
    view_mode_changed = Signal(str)
    sort_changed = Signal(str)
    new_folder_clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("toolbar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 6)
        lay.setSpacing(8)

        self.back_btn = QPushButton()
        self.back_btn.setProperty("class", "icon")
        self.back_btn.setIcon(icon("chevron-left"))
        self.back_btn.setToolTip(i18n.tr("toolbar.back"))
        self.back_btn.setEnabled(False)
        self.back_btn.clicked.connect(self.back_clicked.emit)
        lay.addWidget(self.back_btn)

        self.breadcrumbs = Breadcrumbs()
        lay.addWidget(self.breadcrumbs, 1)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(i18n.tr("search.placeholder"))
        self.search_edit.setFixedWidth(230)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.search_changed.emit)
        lay.addWidget(self.search_edit)

        self.list_btn = QPushButton()
        self.list_btn.setProperty("class", "icon")
        self.list_btn.setCheckable(True)
        self.list_btn.setIcon(icon("list"))
        self.list_btn.setToolTip(i18n.tr("toolbar.view_list"))
        self.grid_btn = QPushButton()
        self.grid_btn.setProperty("class", "icon")
        self.grid_btn.setCheckable(True)
        self.grid_btn.setIcon(icon("grid"))
        self.grid_btn.setToolTip(i18n.tr("toolbar.view_grid"))
        self.list_btn.clicked.connect(lambda: self._view("list"))
        self.grid_btn.clicked.connect(lambda: self._view("grid"))
        lay.addWidget(self.list_btn)
        lay.addWidget(self.grid_btn)

        self.sort_combo = QComboBox()
        for value, key in SORT_VALUES:
            self.sort_combo.addItem(i18n.tr(key), value)
        self.sort_combo.currentIndexChanged.connect(
            lambda idx: self.sort_changed.emit(self.sort_combo.itemData(idx))
        )
        lay.addWidget(self.sort_combo)

        self.new_folder_btn = QPushButton()
        self.new_folder_btn.setProperty("class", "icon")
        self.new_folder_btn.setIcon(icon("folder-plus"))
        self.new_folder_btn.setToolTip(i18n.tr("files.new_folder"))
        self.new_folder_btn.clicked.connect(self.new_folder_clicked.emit)
        lay.addWidget(self.new_folder_btn)

    def _view(self, mode: str) -> None:
        self.list_btn.setChecked(mode == "list")
        self.grid_btn.setChecked(mode == "grid")
        self.view_mode_changed.emit(mode)

    def set_mode(self, mode: str) -> None:
        self._view(mode)

    def set_sort(self, value: str) -> None:
        idx = [v for v, _ in SORT_VALUES].index(value) if value in dict(SORT_VALUES) else 0
        self.sort_combo.blockSignals(True)
        self.sort_combo.setCurrentIndex(idx)
        self.sort_combo.blockSignals(False)

    def set_back_enabled(self, enabled: bool) -> None:
        self.back_btn.setEnabled(enabled)

    def retranslate(self) -> None:
        self.search_edit.setPlaceholderText(i18n.tr("search.placeholder"))
        self.back_btn.setToolTip(i18n.tr("toolbar.back"))
        self.list_btn.setToolTip(i18n.tr("toolbar.view_list"))
        self.grid_btn.setToolTip(i18n.tr("toolbar.view_grid"))
        self.new_folder_btn.setToolTip(i18n.tr("files.new_folder"))
        for i, (value, key) in enumerate(SORT_VALUES):
            self.sort_combo.setItemText(i, i18n.tr(key))
        self.breadcrumbs.set_path(self.breadcrumbs.path)
