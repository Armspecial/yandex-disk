"""Клавиатура: Enter/Delete/Backspace/Menu в списках, «Назад» на страницах.

Регрессия: keyPressEvent в FilesView был объявлен дважды — второй (Enter)
молча перекрывал первый (Delete), и удаление с клавиатуры не работало.
"""

from __future__ import annotations

import os
import warnings
from datetime import datetime, timezone

import pytest


@pytest.fixture()
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _res(name: str, path: str, type_: str = "file"):
    from ydx.core.models import Resource

    return Resource(
        name=name, type=type_, path=path, media_type="image", size=10,
        modified=datetime(2026, 10, 25, 12, tzinfo=timezone.utc),
    )


def _thumbs():
    """Заглушка ThumbService: сигнал ready + пустой pixmap, как в других тестах."""
    from PySide6.QtCore import QObject, Signal

    class _Obj(QObject):
        ready = Signal(str)

        def pixmap(self, *_a, **_k):
            return None

    return _Obj()


class _Settings:
    DEFAULTS = {"view_mode": "list", "sort": "modified"}

    def get(self, key, default=None):
        return self.DEFAULTS.get(key, default)

    def set(self, key, value) -> None:
        pass


class _Acc:
    class _A:
        login = "u"

    active = _A()
    accounts = []
    resources = None


def _list_view(qapp, items):
    """FilesView на простой модели (как в «Недавних»/«Общих»)."""
    from PySide6.QtCore import QItemSelectionModel

    from ydx.services import i18n
    from ydx.ui.views.files_view import FilesView
    from ydx.ui.views.resource_list import ResourceListModel

    i18n.load("ru")
    model = ResourceListModel()
    model.set_items(items)
    view = FilesView(model, _thumbs())
    view.resize(600, 400)
    view.show()
    index = model.index(0, 0)
    view.setCurrentIndex(index)
    view.selectionModel().select(
        index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    return view


# --- FilesView: клавиши в списке -------------------------------------------------


def test_delete_key_requests_delete(qapp):
    """Regression: задавленный вторым keyPressEvent обработчик Delete."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view = _list_view(qapp, [_res("a.jpg", "disk:/a.jpg")])
    got: list = []
    view.delete_requested.connect(lambda sel: got.append(sel))
    QTest.keyClick(view, Qt.Key.Key_Delete)
    assert got and got[0][0].path == "disk:/a.jpg"


def test_enter_opens_selected(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view = _list_view(qapp, [_res("a.jpg", "disk:/a.jpg")])
    got: list = []
    view.open_requested.connect(lambda r: got.append(r))
    QTest.keyClick(view, Qt.Key.Key_Return)
    assert got and got[0].path == "disk:/a.jpg"


def test_backspace_and_back_key_request_back(qapp):
    """Backspace и аппаратная клавиша Back — «Назад», а не удаление."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view = _list_view(qapp, [_res("a.jpg", "disk:/a.jpg")])
    back = []
    deleted = []
    view.back_requested.connect(lambda: back.append(True))
    view.delete_requested.connect(lambda sel: deleted.append(sel))
    QTest.keyClick(view, Qt.Key.Key_Backspace)
    QTest.keyClick(view, Qt.Key.Key_Back)
    assert len(back) == 2
    assert not deleted


def test_menu_key_opens_context_menu(qapp):
    """Menu/Shift+F10 — контекстное меню без мыши."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view = _list_view(qapp, [_res("a.jpg", "disk:/a.jpg")])
    got: list = []
    view.context_menu_requested.connect(lambda pos, sel: got.append((pos, sel)))
    QTest.keyClick(view, Qt.Key.Key_Menu)
    assert got and got[0][1][0].path == "disk:/a.jpg"

    QTest.keyClick(view, Qt.Key.Key_F10, Qt.KeyboardModifier.ShiftModifier)
    assert len(got) == 2


# --- Файлы: «Назад»/«Выше»/F2/Ctrl+F ---------------------------------------------


def _files_page(qapp):
    from ydx.services import i18n
    from ydx.ui.views.files_page import FilesPage

    i18n.load("ru")
    return FilesPage(_Settings(), _Acc(), _thumbs(), None)


def test_backspace_goes_back_in_files(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    page = _files_page(qapp)
    page.load_path("disk:/Фото")
    assert page.toolbar.back_btn.isEnabled()

    QTest.keyClick(page.view, Qt.Key.Key_Backspace)
    assert page._current == "disk:/"
    assert not page.toolbar.back_btn.isEnabled()


def test_go_up_to_parent(qapp):
    page = _files_page(qapp)
    page.load_path("disk:/a/b")
    page._go_up()
    assert page._current == "disk:/a"
    page._go_up()
    assert page._current == "disk:/"
    page._go_up()                      # в корне — no-op
    assert page._current == "disk:/"


def test_f2_renames_only_single_selection(qapp, monkeypatch):
    page = _files_page(qapp)
    res = _res("a.jpg", "disk:/a.jpg")
    calls: list = []
    monkeypatch.setattr(page, "rename", lambda r: calls.append(r))

    monkeypatch.setattr(page.view, "selected_resources", lambda: [])
    page._rename_selected()
    monkeypatch.setattr(page.view, "selected_resources", lambda: [res, _res("b", "disk:/b")])
    page._rename_selected()
    assert calls == []                 # пусто и много — не переименовываем

    monkeypatch.setattr(page.view, "selected_resources", lambda: [res])
    page._rename_selected()
    assert calls == [res]


def test_focus_search(qapp):
    page = _files_page(qapp)
    page.show()
    with warnings.catch_warnings():    # offscreen: фокус даёт только setActiveWindow
        warnings.simplefilter("ignore", DeprecationWarning)
        qapp.setActiveWindow(page)
    page._focus_search()
    assert page.toolbar.search_edit.hasFocus()


# --- Корзина: «Назад» с клавиатуры ------------------------------------------------


def test_backspace_goes_back_in_trash(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from ydx.services import i18n
    from ydx.ui.views.trash_page import TrashPage

    i18n.load("ru")
    page = TrashPage(_Acc(), _thumbs())
    page._path = "trash:/sub"
    page._history = ["/"]
    page.back_btn.setEnabled(True)

    QTest.keyClick(page.view, Qt.Key.Key_Backspace)
    assert page._path == "/"
    assert not page.back_btn.isEnabled()


# --- Фото: таймлайн ----------------------------------------------------------------


def _timeline(qapp, photos):
    from PySide6.QtCore import QItemSelectionModel

    from ydx.services import i18n
    from ydx.ui.views.photo_timeline import PhotoTimelineView

    i18n.load("ru")
    view = PhotoTimelineView(_thumbs())
    view.set_photos(photos)
    view.resize(800, 600)
    view.show()
    # строка 0 — заголовок дня, строка 1 — сами фото
    index = view.model().index(1, 0)
    view.setCurrentIndex(index)
    view.selectionModel().select(
        index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    return view


def test_menu_key_opens_context_menu_in_timeline(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view = _timeline(qapp, [_res("a.jpg", "disk:/a.jpg")])
    got: list = []
    view.context_menu_requested.connect(lambda pos, sel: got.append((pos, sel)))
    QTest.keyClick(view, Qt.Key.Key_Menu)
    assert got and got[0][1][0].path == "disk:/a.jpg"


def test_timeline_delete_only_with_delete_key(qapp):
    """Backspace в таймлайне больше не удаляет (он — «Назад»), Delete — удаляет."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    view = _timeline(qapp, [_res("a.jpg", "disk:/a.jpg")])
    deleted: list = []
    view.delete_requested.connect(lambda sel: deleted.append(sel))
    QTest.keyClick(view, Qt.Key.Key_Backspace)
    assert not deleted
    QTest.keyClick(view, Qt.Key.Key_Delete)
    assert deleted and deleted[0][0].path == "disk:/a.jpg"
