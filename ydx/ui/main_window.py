"""Главное окно приложения (каркас M0; наполняется в M1+)."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QMainWindow

from ydx import __version__


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("mainWindow")
        self.setWindowTitle(f"Яндекс Диск — {__version__}")
        self.resize(1200, 780)
        self.setCentralWidget(QLabel("Каркас приложения (M0)"))
