"""Окно «Ошибки»: всё, что пошло не так, в одном списке."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ydx.services import i18n
from ydx.services.error_center import ErrorCenter

SOURCE_KEYS = {
    "files": "errors.source.files",
    "transfers": "errors.source.transfers",
    "sync": "errors.source.sync",
    "auth": "errors.source.auth",
}


class ErrorsDialog(QDialog):
    def __init__(self, center: ErrorCenter, parent=None) -> None:
        super().__init__(parent)
        self._center = center
        self.setModal(True)
        self.setMinimumSize(640, 460)
        self.setWindowTitle(i18n.tr("errors.title"))

        col = QVBoxLayout(self)
        col.setContentsMargins(20, 18, 20, 14)
        col.setSpacing(10)

        self.header = QLabel()
        self.header.setObjectName("dialogHint")
        col.addWidget(self.header)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([
            i18n.tr("errors.col.time"), i18n.tr("errors.col.source"),
            i18n.tr("errors.col.message"),
        ])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(1, 110)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        col.addWidget(self.table, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        clear = QPushButton(i18n.tr("errors.clear"))
        clear.clicked.connect(self._center.clear)
        close = QPushButton(i18n.tr("common.close"))
        close.setProperty("class", "primary")
        close.clicked.connect(self.accept)
        row.addWidget(clear)
        row.addWidget(close)
        col.addLayout(row)

        center.changed.connect(self.rebuild)
        self.rebuild()

    def rebuild(self) -> None:
        errors = self._center.errors
        self.header.setText(
            i18n.tr("errors.count", n=len(errors)) if errors
            else i18n.tr("errors.empty")
        )
        self.table.setRowCount(len(errors))
        for i, err in enumerate(errors):
            self.table.setItem(i, 0, QTableWidgetItem(err.time_str))
            self.table.setItem(
                i, 1, QTableWidgetItem(i18n.tr(SOURCE_KEYS.get(err.source, "errors.source.other")))
            )
            self.table.setItem(i, 2, QTableWidgetItem(err.message))
        self.table.setRowCount(max(len(errors), 0))
