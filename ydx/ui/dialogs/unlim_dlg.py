"""Диалог «Безлимитная автозагрузка»: вставка кук браузерной сессии.

Куки дают доступ к ленте photounlim, которую публичный REST API не отдаёт.
Вставляется строка Cookie-заголовка из DevTools браузера; хранится в 0600.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ydx.core.unlim_api import PHOTOS_URL
from ydx.services import i18n
from ydx.services.settings import AppSettings
from ydx.services.unlim import UnlimService, dump_path


class UnlimDialog(QDialog):
    def __init__(self, settings: AppSettings, unlim: UnlimService, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._unlim = unlim

        self.setModal(True)
        self.setMinimumSize(520, 380)
        self.setWindowTitle(i18n.tr("unlim.title"))

        col = QVBoxLayout(self)
        col.setContentsMargins(22, 20, 22, 18)
        col.setSpacing(12)

        self.help_label = QLabel(i18n.tr("unlim.help"))
        self.help_label.setObjectName("dialogHint")
        self.help_label.setWordWrap(True)
        col.addWidget(self.help_label)

        # сразу открыть веб-версию — там вход и копирование Cookie из DevTools
        self.open_web_btn = QPushButton()
        self.open_web_btn.clicked.connect(self._open_web)
        col.addWidget(self.open_web_btn)

        # куки берутся из профиля браузера сами — без F12 и копирования
        self.autofind_btn = QPushButton()
        self.autofind_btn.setProperty("class", "primary")
        self.autofind_btn.clicked.connect(self._autofind)
        col.addWidget(self.autofind_btn)

        self.cookie_edit = QLineEdit()
        self.cookie_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.cookie_edit.setPlaceholderText(i18n.tr("unlim.paste_placeholder"))
        col.addWidget(self.cookie_edit)

        self.status_label = QLabel()
        self.status_label.setObjectName("dialogHint")
        self.status_label.setWordWrap(True)
        col.addWidget(self.status_label)

        self.show_check = QCheckBox()
        self.show_check.setChecked(bool(settings.get("show_unlim")))
        self.show_check.toggled.connect(
            lambda on: settings.set("show_unlim", on)
        )
        col.addWidget(self.show_check)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.check_btn = QPushButton()
        self.check_btn.setProperty("class", "primary")
        self.check_btn.clicked.connect(self._check)
        btn_row.addWidget(self.check_btn)
        self.save_btn = QPushButton()
        self.save_btn.clicked.connect(self._save)
        btn_row.addWidget(self.save_btn)
        self.delete_btn = QPushButton()
        self.delete_btn.setProperty("class", "danger")
        self.delete_btn.clicked.connect(self._delete)
        btn_row.addWidget(self.delete_btn)
        self.diag_btn = QPushButton()
        self.diag_btn.clicked.connect(self._diagnose)
        btn_row.addWidget(self.diag_btn)
        col.addLayout(btn_row)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        self.close_btn = QPushButton()
        self.close_btn.clicked.connect(self.accept)
        close_row.addWidget(self.close_btn)
        col.addLayout(close_row)

        i18n.register(self.retranslate)
        self.retranslate()
        self._refresh_status()

    # --- обработчики ------------------------------------------------------------

    def _open_web(self) -> None:
        """Открыть раздел «Фото» веб-диска — источник кук для вставки."""
        QDesktopServices.openUrl(QUrl(PHOTOS_URL))

    def _refresh_status(self) -> None:
        self.status_label.setText(
            i18n.tr("unlim.status_set") if self._unlim.has_cookies
            else i18n.tr("unlim.status_none")
        )

    def _check(self) -> None:
        from ydx.services.workers import run_in_thread

        raw = self.cookie_edit.text().strip()
        if raw:
            job = lambda: self._unlim.check(raw)                    # noqa: E731
        elif self._unlim.has_cookies:
            job = self._unlim.check_stored                          # поле пусто — проверим сохранённые
        else:
            self._refresh_status()
            return

        def failed(exc: Exception) -> None:
            self.status_label.setText(_error_text(exc))

        run_in_thread(
            job,
            lambda n: self.status_label.setText(i18n.tr("unlim.check_ok", n=n)),
            failed,
        )

    def _autofind(self) -> None:
        """Куки из локального браузера + проверка: сколько элементов в безлимите."""
        from ydx.services.workers import run_in_thread

        def job() -> tuple[str, int] | None:
            adopted = self._unlim.adopt_browser_cookies()
            if adopted is None:
                return None
            browser, header, _changed = adopted
            return browser, self._unlim.check(header)

        def done(result) -> None:
            self.cookie_edit.clear()
            self._refresh_status()                     # «куки подключены» — фон
            if result is None:
                self.status_label.setText(i18n.tr("unlim.autofind_none"))
            else:
                browser, count = result
                self.status_label.setText(
                    i18n.tr("unlim.autofind_ok", browser=browser, n=count)
                )

        run_in_thread(
            job, done,
            lambda exc: self.status_label.setText(_error_text(exc)),
        )

    def _save(self) -> None:
        raw = self.cookie_edit.text().strip()
        try:
            self._unlim.save_cookies(raw)
        except ValueError:
            self.status_label.setText(i18n.tr("unlim.cookies_invalid"))
            return
        except Exception as exc:  # noqa: BLE001 — нет активного аккаунта и пр.
            self.status_label.setText(_error_text(exc))
            return
        self.cookie_edit.clear()
        self._refresh_status()

    def _delete(self) -> None:
        self._unlim.clear_cookies()
        self.cookie_edit.clear()
        self._refresh_status()

    def _diagnose(self) -> None:
        """Сохранить сырые ответы внутреннего API — уточнение полей элементов."""
        from ydx.services.workers import run_in_thread

        raw = self.cookie_edit.text().strip()
        source = raw if raw else None

        def job() -> str:
            dump = self._unlim.diagnostic_dump(source)
            path = dump_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(dump, ensure_ascii=False, indent=2), "utf-8"
            )
            return str(path)

        run_in_thread(
            job,
            lambda path: self.status_label.setText(
                i18n.tr("unlim.diag_saved", path=path)
            ),
            lambda exc: self.status_label.setText(_error_text(exc)),
        )

    def retranslate(self) -> None:
        self.setWindowTitle(i18n.tr("unlim.title"))
        self.help_label.setText(i18n.tr("unlim.help"))
        self.open_web_btn.setText(i18n.tr("unlim.open_web"))
        self.autofind_btn.setText(i18n.tr("unlim.autofind"))
        self.cookie_edit.setPlaceholderText(i18n.tr("unlim.paste_placeholder"))
        self.show_check.setText(i18n.tr("settings.show_unlim"))
        self.check_btn.setText(i18n.tr("unlim.check"))
        self.save_btn.setText(i18n.tr("unlim.save"))
        self.delete_btn.setText(i18n.tr("unlim.delete"))
        self.diag_btn.setText(i18n.tr("unlim.diagnose"))
        self.close_btn.setText(i18n.tr("common.close"))

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API
        i18n.unregister(self.retranslate)
        super().closeEvent(event)


def _error_text(exc: Exception) -> str:
    from ydx.ui.errors_ui import api_error_text

    return api_error_text(exc)
