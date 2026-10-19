"""Диалоги файловых операций: ввод имени, подтверждение удаления, свойства, выбор папки."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ydx.core.api_resources import ResourcesApi
from ydx.core.errors import ApiError
from ydx.services import i18n
from ydx.services.fmt import human_date, human_size
from ydx.services.thumbs import ThumbService, thumb_key
from ydx.services.workers import run_in_thread
from ydx.ui.errors_ui import api_error_text


def ask_name(
    parent: QWidget | None, title: str, label: str, initial: str = "",
) -> str | None:
    """Диалог ввода имени (новая папка / переименование). None = отмена."""
    dlg = QDialog(parent)
    dlg.setModal(True)
    dlg.setMinimumWidth(420)
    dlg.setWindowTitle(title)
    col = QVBoxLayout(dlg)
    col.setContentsMargins(20, 18, 20, 16)
    col.setSpacing(10)
    lab = QLabel(label)
    edit = QLineEdit(initial)
    edit.selectAll()
    col.addWidget(lab)
    col.addWidget(edit)
    error = QLabel()
    error.setObjectName("errorBanner")
    error.hide()
    col.addWidget(error)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    col.addWidget(buttons)
    result = dlg.exec()
    if result != QDialog.DialogCode.Accepted:
        return None
    name = edit.text().strip()
    return name or None


class PropertiesDialog(QDialog):
    def __init__(self, resource, thumbs: ThumbService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setModal(True)
        self.setMinimumSize(420, 460)
        self.setWindowTitle(resource.name)
        col = QVBoxLayout(self)
        col.setContentsMargins(22, 20, 22, 18)
        col.setSpacing(12)

        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(360, 240)
        col.addWidget(self.preview)

        form = QFormLayout()
        form.setSpacing(8)
        form.addRow(self._cap("props.type"), QLabel(
            i18n.tr("props.folder") if resource.is_dir
            else (resource.media_type or resource.mime_type or i18n.tr("props.file"))
        ))
        if not resource.is_dir:
            form.addRow(self._cap("props.size"), QLabel(human_size(resource.size)))
        if resource.created:
            form.addRow(self._cap("props.created"), QLabel(human_date(resource.created)))
        if resource.modified:
            form.addRow(self._cap("props.modified"), QLabel(human_date(resource.modified)))
        form.addRow(self._cap("props.path"), QLabel(resource.path))
        if resource.public_url:
            link = QLabel(f'<a href="{resource.public_url}">{resource.public_url}</a>')
            link.setOpenExternalLinks(True)
            form.addRow(self._cap("props.link"), link)
        col.addLayout(form)
        col.addStretch(1)
        close = QPushButton(i18n.tr("common.close"))
        close.clicked.connect(self.accept)
        col.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

        pm = thumbs.pixmap(resource, 360) if resource.preview else None
        if pm is not None:
            self._set_pixmap(pm)
        elif resource.preview:
            key = thumb_key(resource)

            def on_ready(k: str) -> None:
                if k == key:
                    pm2 = thumbs.pixmap(resource, 360)
                    if pm2 is not None:
                        self._set_pixmap(pm2)

            thumbs.ready.connect(on_ready)

    def _set_pixmap(self, pm) -> None:
        self.preview.setPixmap(pm.scaled(
            360, 240, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    @staticmethod
    def _cap(key: str) -> QLabel:
        lab = QLabel(i18n.tr(key))
        lab.setObjectName("dialogHint")
        return lab


class FolderPickerDialog(QDialog):
    """Выбор папки на Диске: дерево с ленивой подгрузкой (только каталоги)."""

    def __init__(
        self, resources: ResourcesApi, start_path: str = "disk:/",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setMinimumSize(460, 520)
        self.setWindowTitle(i18n.tr("folderpick.title"))
        self._resources = resources
        self.selected_path: str | None = None

        col = QVBoxLayout(self)
        col.setContentsMargins(18, 16, 18, 14)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemExpanded.connect(self._populate_children)
        self.tree.itemSelectionChanged.connect(self._on_select)
        col.addWidget(self.tree, 1)

        error = QLabel()
        error.setObjectName("errorBanner")
        error.hide()
        self._error = error
        col.addWidget(error)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        col.addWidget(buttons)

        self._load_root()

    # --- загрузка дерева ----------------------------------------------------------

    def _load_root(self) -> None:
        def job():
            items, _ = self._resources.list_page("disk:/", limit=500)
            return [r for r in items if r.is_dir]

        run_in_thread(job, self._fill_root, self._show_error)

    def _fill_root(self, dirs: list) -> None:
        root = QTreeWidgetItem([i18n.tr("props.disk_root")])
        root.setData(0, Qt.ItemDataRole.UserRole, "disk:/")
        self.tree.addTopLevelItem(root)
        self._add_children(root, dirs)
        root.setExpanded(True)

    def _populate_children(self, item: QTreeWidgetItem) -> None:
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if item.childCount() != 1 or item.child(0).data(0, Qt.ItemDataRole.UserRole) is not None:
            return   # уже загружено

        def job():
            items, _ = self._resources.list_page(path, limit=500)
            return path, [r for r in items if r.is_dir]

        run_in_thread(job, lambda res: self._fill_children(item, res[0], res[1]), self._show_error)

    def _fill_children(self, item: QTreeWidgetItem, path: str, dirs: list) -> None:
        dummy = item.child(0)
        if dummy is not None and dummy.data(0, Qt.ItemDataRole.UserRole) is None:
            item.removeChild(dummy)
        self._add_children(item, dirs)

    @staticmethod
    def _add_children(item: QTreeWidgetItem, dirs: list) -> None:
        for d in dirs:
            child = QTreeWidgetItem([d.name])
            child.setData(0, Qt.ItemDataRole.UserRole, d.path)
            item.addChild(child)
            if d.total is None or d.total > 0:   # может содержать подпапки
                placeholder = QTreeWidgetItem(["…"])
                placeholder.setData(0, Qt.ItemDataRole.UserRole, None)
                child.addChild(placeholder)

    def _on_select(self) -> None:
        item = self.tree.currentItem()
        if item is not None:
            self.selected_path = item.data(0, Qt.ItemDataRole.UserRole)

    def _show_error(self, exc: Exception) -> None:
        self._error.setText(api_error_text(exc))
        self._error.show()
