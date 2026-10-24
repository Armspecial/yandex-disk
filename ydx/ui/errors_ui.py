"""Дружелюбный показ ошибок API в UI."""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from ydx.core.errors import (
    ApiError,
    AuthError,
    ConflictError,
    DiskFullError,
    NetworkError,
    NotFoundError,
    QuotaUploadLimitError,
    UnlimAuthError,
    UnlimCaptchaError,
    UnlimError,
)
from ydx.services import i18n


_center = None


def attach_center(center) -> None:
    """Подключить центр ошибок: все показанные ошибки попадут и туда."""
    global _center
    _center = center


def api_error_text(exc: Exception) -> str:
    if isinstance(exc, QuotaUploadLimitError):
        return i18n.tr("err.upload_limit")
    if isinstance(exc, AuthError):
        return i18n.tr("err.auth")
    if isinstance(exc, NetworkError):
        return i18n.tr("err.network")
    if isinstance(exc, ConflictError):
        return i18n.tr("err.conflict")
    if isinstance(exc, DiskFullError):
        return i18n.tr("err.disk_full")
    if isinstance(exc, NotFoundError):
        return i18n.tr("err.not_found")
    if isinstance(exc, UnlimAuthError):
        return i18n.tr("unlim.err_expired")
    if isinstance(exc, UnlimCaptchaError):
        return i18n.tr("unlim.err_captcha")
    if isinstance(exc, UnlimError):
        return i18n.tr("err.unknown", err=exc)
    if isinstance(exc, ApiError):
        return f"{exc.code}: {exc.description}"
    return i18n.tr("err.unknown", err=exc)


def show_api_error(parent: QWidget | None, exc: Exception, title: str | None = None) -> None:
    if _center is not None:
        _center.report("files", api_error_text(exc))
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle(title or i18n.tr("app.title"))
    box.setText(api_error_text(exc))
    box.exec()


def confirm(parent: QWidget | None, text: str, title: str | None = None) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title or i18n.tr("app.title"))
    box.setText(text)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes
