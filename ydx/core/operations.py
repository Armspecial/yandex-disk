"""Поллинг асинхронных операций API (ответы 202).

Блокирующие функции — вызывать из воркеров. Растущий интервал 1→2→5 c,
таймаут 5 минут. failed → ApiError (вызывающий повторяет исходный запрос).
"""

from __future__ import annotations

import time

from ydx.core.api_client import ApiClient
from ydx.core.errors import ApiError
from ydx.core.models import Operation

INTERVALS = (1.0, 2.0, 5.0)
MAX_WAIT = 300.0


def wait_operation(
    client: ApiClient, op: Operation,
    should_cancel=None, on_status=None,
) -> str:
    """Дождаться завершения операции; вернуть финальный статус.

    should_cancel() -> bool позволяет прервать ожидание (отмена в UI).
    on_status(Operation) вызывается после каждого опроса.
    """
    deadline = time.monotonic() + MAX_WAIT
    idx = 0
    while True:
        d = client.get(op.href)          # href — абсолютный или относительный к API
        status = d.get("status", "in-progress")
        op = Operation(href=op.href, status=status)
        if on_status:
            on_status(op)
        if op.done:
            if op.status == "failed":
                raise ApiError(202, "OperationFailed", "async operation failed")
            return op.status
        if should_cancel and should_cancel():
            return "cancelled"
        if time.monotonic() > deadline:
            raise ApiError(202, "OperationTimeout", "operation status wait timeout")
        time.sleep(INTERVALS[min(idx, len(INTERVALS) - 1)])
        idx += 1
