"""Иерархия ошибок приложения.

core не знает про Qt; UI сопоставляет классы ошибок с действиями
(перелогин, повтор, подсказка про лимит) и локализованными текстами.
"""

from __future__ import annotations


class YdxError(Exception):
    """Базовая ошибка приложения."""


class AuthError(YdxError):
    """Проблема авторизации: токена нет, истёк/отозван, OAuth не завершён."""


class NetworkError(YdxError):
    """Сетевая недоступность / таймаут."""


class ApiError(YdxError):
    """API вернул ошибку. code — машинное имя DiskNotFoundError и т.п."""

    def __init__(self, status: int, code: str, description: str) -> None:
        self.status = status
        self.code = code or "unknown"
        self.description = description
        super().__init__(f"[{status}] {self.code}: {description}")


class NotFoundError(ApiError):
    pass


class ConflictError(ApiError):
    """409 — имя занято и overwrite=False."""


class DiskFullError(ApiError):
    """507 — место закончилось."""


class QuotaUploadLimitError(YdxError):
    """Файл больше лимита тарифа (1 ГБ бесплатно / 50 ГБ с Яндекс 360)."""


class CancelledError(YdxError):
    """Передача отменена пользователем."""


def specialize(status: int, code: str, description: str) -> ApiError:
    """Собрать типизированный ApiError по статусу/коду API."""
    cls: type[ApiError] = ApiError
    if status == 404 or code == "DiskNotFoundError":
        cls = NotFoundError
    elif status == 409:
        cls = ConflictError
    elif status == 507:
        cls = DiskFullError
    return cls(status, code, description)
