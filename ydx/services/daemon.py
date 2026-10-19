"""Управление официальным демоном yandex-disk через QProcess.

Свой конфиг ~/.config/yandex-disk-linux/daemon.cfg — официальный
~/.config/yandex-disk/ не трогаем. Демонд опционален: нет бинаря —
панель скрыта; ошибка вида «нужна подписка» — состояние paywall.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from ydx.constants import APP_ID, DAEMON_BINARY, DAEMON_CONFIG_FILE
from ydx.core.daemon_config import read_config, write_config
from ydx.core.daemon_status import looks_like_paywall, parse_status
from ydx.core.models import DaemonStatus

POLL_MS = 2000
WATCH_DEBOUNCE_MS = 800


class DaemonController(QObject):
    status_updated = Signal(object)        # DaemonStatus
    paywall_detected = Signal()
    availability_changed = Signal(bool)    # установлен/не установлен

    def __init__(self, accounts, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._accounts = accounts
        self._installed = shutil.which(DAEMON_BINARY) is not None
        self._pause_timer: QTimer | None = None
        self._watch_debounce = QTimer(self)
        self._watch_debounce.setSingleShot(True)
        self._watch_debounce.setInterval(WATCH_DEBOUNCE_MS)
        self._watch_debounce.timeout.connect(self.refresh_status)
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_MS)
        self._poll.timeout.connect(self.refresh_status)
        self._procs: list[QProcess] = []

    # --- доступность ------------------------------------------------------------

    @property
    def installed(self) -> bool:
        return self._installed

    # --- конфиг ------------------------------------------------------------------

    @property
    def config_path(self) -> Path:
        return DAEMON_CONFIG_FILE

    def config(self) -> dict[str, str]:
        return read_config(DAEMON_CONFIG_FILE)

    def ensure_config(self) -> bool:
        """Создать/пополнить daemon.cfg и обновить файл токена.

        Папку (dir) НЕ трогаем, если она уже задана — иначе затёрли бы
        выбор пользователя из диалога синхронизации.
        """
        token = self._accounts.current_token()
        if not token:
            return False
        cfg = read_config(DAEMON_CONFIG_FILE)
        sync_dir = cfg.get("dir") or str(Path.home() / "Yandex.Disk")
        Path(sync_dir).mkdir(parents=True, exist_ok=True)
        passwd = DAEMON_CONFIG_FILE.parent / "passwd"
        passwd.write_text(token + "\n", "utf-8")
        try:
            passwd.chmod(0o600)
        except OSError:
            pass
        write_config(DAEMON_CONFIG_FILE, {
            "dir": sync_dir,
            "auth": str(passwd),
        })
        return True

    def apply_config(self, updates: dict[str, str]) -> None:
        write_config(DAEMON_CONFIG_FILE, updates)

    # --- жизненный цикл демона -------------------------------------------------------

    def _run(self, args: list[str], on_output=None) -> None:
        proc = QProcess(self)
        self._procs.append(proc)

        def cleanup() -> None:
            if proc in self._procs:
                self._procs.remove(proc)
            proc.deleteLater()

        def finished(_code: int, _status) -> None:
            output = str(proc.readAllStandardOutput(), "utf-8", "replace")
            cleanup()
            if on_output is not None:
                on_output(output)
            QTimer.singleShot(300, self.refresh_status)

        def errored(err) -> None:
            # при FailedToStart finished НЕ приходит (проверено) — чистим сами
            cleanup()
            QTimer.singleShot(300, self.refresh_status)

        proc.finished.connect(finished)
        proc.errorOccurred.connect(errored)
        proc.start(DAEMON_BINARY, args)

    def start(self) -> None:
        self.cancel_pause()
        self.ensure_config()
        self._run(["start", "-c", str(DAEMON_CONFIG_FILE)])

    def stop(self) -> None:
        self.cancel_pause()   # явный стоп отменяет автозапуск паузы
        self._run(["stop", "-c", str(DAEMON_CONFIG_FILE)])

    def pause_for(self, hours: float) -> None:
        """Демонд не умеет паузу: стоп + автозапуск по таймеру."""
        self._run(["stop", "-c", str(DAEMON_CONFIG_FILE)])
        if self._pause_timer is not None:
            self._pause_timer.stop()
        self._pause_timer = QTimer(self)
        self._pause_timer.setSingleShot(True)
        self._pause_timer.setInterval(int(hours * 3600 * 1000))
        self._pause_timer.timeout.connect(self.start)
        self._pause_timer.start()

    def cancel_pause(self) -> None:
        if self._pause_timer is not None:
            self._pause_timer.stop()
            self._pause_timer = None

    # --- статус -------------------------------------------------------------------------

    def refresh_status(self) -> None:
        if not self._installed:
            return
        proc = QProcess(self)
        self._procs.append(proc)

        def emit_status(status) -> None:
            if looks_like_paywall(status):
                status.state = "paywall"
                self.paywall_detected.emit()
            self.status_updated.emit(status)

        def cleanup() -> None:
            if proc in self._procs:
                self._procs.remove(proc)
            proc.deleteLater()

        def finished(_code: int, _status) -> None:
            output = str(proc.readAllStandardOutput(), "utf-8", "replace")
            stderr = str(proc.readAllStandardError(), "utf-8", "replace")
            cleanup()
            emit_status(parse_status(output or stderr))

        def errored(_err) -> None:
            cleanup()
            emit_status(DaemonStatus(state="stopped"))

        proc.finished.connect(finished)
        proc.errorOccurred.connect(errored)
        proc.start(DAEMON_BINARY, ["status", "-c", str(DAEMON_CONFIG_FILE)])

    def shutdown(self) -> None:
        """Аккуратно завершить фоновые QProcess при выходе."""
        self._poll.stop()
        for proc in list(self._procs):
            proc.kill()
        self._procs.clear()

    def watch(self) -> None:
        """Следить за .sync/cli.log внутри папки демона + периодический опрос."""
        from PySide6.QtCore import QFileSystemWatcher

        cfg = self.config()
        sync_dir = cfg.get("dir", "")
        if sync_dir:
            log = Path(sync_dir) / ".sync" / "cli.log"
            if log.exists():
                watcher = QFileSystemWatcher(self)
                watcher.addPath(str(log))
                watcher.fileChanged.connect(lambda _p: self._watch_debounce.start())
                self._file_watcher = watcher
        if not self._poll.isActive():
            self._poll.start()
        self.refresh_status()
