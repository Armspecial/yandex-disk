"""Страница «Загрузки»: очередь передач с прогрессом и управлением."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ydx.core.models import TransferTask
from ydx.services import i18n
from ydx.services.fmt import human_size, human_speed
from ydx.services.transfers import TransferEngine
from ydx.ui.widgets import ElidedLabel, icon


class TaskRow(QWidget):
    action_requested = Signal(int, str)   # task_id, действие pause/resume/cancel/retry/overwrite

    def __init__(self, task: TransferTask, parent=None) -> None:
        super().__init__(parent)
        self.task_id = task.id
        self.setFixedHeight(64)

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 8, 14, 8)
        root.setSpacing(12)

        self.icon_label = QLabel()
        self.icon_label.setFixedSize(24, 24)
        root.addWidget(self.icon_label)

        middle = QVBoxLayout()
        middle.setSpacing(2)
        top = QHBoxLayout()
        self.name_label = ElidedLabel()
        top.addWidget(self.name_label, 1)
        self.status_label = QLabel()
        self.status_label.setObjectName("quotaLabel")
        top.addWidget(self.status_label)
        middle.addLayout(top)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        middle.addWidget(self.bar)
        root.addLayout(middle, 1)

        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(4)
        root.addLayout(self.buttons)

        self._state_widgets: dict[str, QPushButton] = {}
        for action, ic in (("pause", "pause"), ("resume", "resume"),
                           ("cancel", "close"), ("retry", "refresh"),
                           ("overwrite", "upload")):
            btn = QPushButton()
            btn.setProperty("class", "icon")
            btn.setIcon(icon(ic))
            btn.setFixedSize(30, 30)
            btn.clicked.connect(
                lambda _=False, a=action: self.action_requested.emit(self.task_id, a)
            )
            btn.hide()
            self._state_widgets[action] = btn
            self.buttons.addWidget(btn)

        self.update_task(task)

    def update_task(self, task: TransferTask) -> None:
        self.icon_label.setPixmap(
            icon("upload" if task.kind == "upload" else "download").pixmap(20, 20)
        )
        name = task.local_path.rsplit("/", 1)[-1] if task.kind == "upload" else task.remote_path.rsplit("/", 1)[-1]
        self.name_label.setText(name)
        size = task.size or 0
        done = task.transferred
        if task.state == "done":
            self.bar.setRange(0, 1)
            self.bar.setValue(1)
            self.status_label.setText(human_size(size))
        else:
            self.bar.setRange(0, max(size, 1))
            self.bar.setValue(min(done, size))
            if task.state == "running" and size:
                self.status_label.setText(
                    i18n.tr("transfers.progress", done=human_size(done), total=human_size(size))
                )
            else:
                self.status_label.setText(self._state_text(task))
        self._show_buttons(task.state)

    @staticmethod
    def _state_text(task: TransferTask) -> str:
        return i18n.tr(f"transfers.state.{task.state}")

    def _show_buttons(self, state: str) -> None:
        for b in self._state_widgets.values():
            b.hide()
        if state in ("running", "queued"):
            self._state_widgets["pause"].setVisible(state == "running")
            self._state_widgets["cancel"].show()
        elif state == "paused":
            self._state_widgets["resume"].show()
            self._state_widgets["cancel"].show()
        elif state in ("error", "cancelled"):
            self._state_widgets["retry"].show()
        elif state == "conflict":
            self._state_widgets["overwrite"].show()
            self._state_widgets["retry"].show()


class TransfersView(QWidget):
    def __init__(self, engine: TransferEngine, parent=None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._rows: dict[int, TaskRow] = {}

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        header = QWidget()
        hlay = QHBoxLayout(header)
        hlay.setContentsMargins(16, 12, 16, 8)
        self.title = QLabel()
        self.title.setObjectName("dialogTitle")
        hlay.addWidget(self.title)
        hlay.addStretch(1)
        self.clear_btn = QPushButton()
        self.clear_btn.clicked.connect(engine.clear_finished)
        hlay.addWidget(self.clear_btn)
        col.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.container = QWidget()
        self.list_layout = QVBoxLayout(self.container)
        self.list_layout.setContentsMargins(8, 4, 8, 8)
        self.list_layout.setSpacing(4)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.container)
        col.addWidget(self.scroll, 1)

        self.empty_label = QLabel()
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self.empty_label, 1)
        self.scroll.hide()
        self.empty_label.show()

        engine.task_updated.connect(self._update_row)
        engine.tasks_changed.connect(self._rebuild)
        engine.batch_done.connect(self._rebuild)
        self._rebuild()
        i18n.register(self.retranslate)
        self.retranslate()

    # --- обновление ---------------------------------------------------------------

    def on_shown(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        tasks = self._engine.tasks
        if not tasks:
            self.scroll.hide()
            self.empty_label.show()
            return
        self.scroll.show()
        self.empty_label.hide()
        alive = {t.id for t in tasks}
        for tid in list(self._rows):
            if tid not in alive:
                row = self._rows.pop(tid)
                row.deleteLater()
        for t in tasks:
            if t.id not in self._rows:
                row = TaskRow(t)
                row.action_requested.connect(self._on_action)
                self._rows[t.id] = row
                self.list_layout.insertWidget(self.list_layout.count() - 1, row)
            else:
                self._rows[t.id].update_task(t)
        # сводка в заголовке
        active = sum(1 for t in tasks if t.state in ("running", "queued", "paused"))
        self.title.setText(
            i18n.tr("transfers.title_active", n=active) if active else i18n.tr("nav.transfers")
        )

    def _update_row(self, task_id: int) -> None:
        row = self._rows.get(task_id)
        if row is None:
            self._rebuild()
            return
        for t in self._engine.tasks:
            if t.id == task_id:
                row.update_task(t)
                return

    def _on_action(self, task_id: int, action: str) -> None:
        if action == "pause":
            self._engine.pause(task_id)
        elif action == "resume":
            self._engine.resume(task_id)
        elif action == "cancel":
            self._engine.cancel(task_id)
        elif action == "retry":
            self._engine.retry(task_id)
        elif action == "overwrite":
            self._engine.retry(task_id, overwrite=True)

    def retranslate(self) -> None:
        self.title.setText(i18n.tr("nav.transfers"))
        self.clear_btn.setText(i18n.tr("transfers.clear_completed"))
        self.empty_label.setText(i18n.tr("transfers.empty"))
