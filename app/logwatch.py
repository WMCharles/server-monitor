from __future__ import annotations

import logging
from pathlib import Path

from .alerts import AlertEngine, AlertEvent
from .config import Settings
from .monitor import ServerMonitor
from .storage import Storage

logger = logging.getLogger(__name__)

CURSOR_PREFIX = "logcursor:"


class AppLogWatcher:
    """Tails allowlisted application log files and alerts on new error lines.

    A byte-offset cursor per file is persisted in the state DB, so each check
    only inspects bytes appended since the previous check. The first run for a
    file records the current size and stays silent, avoiding alerts on
    historical content.
    """

    def __init__(
        self,
        settings: Settings,
        monitor: ServerMonitor,
        storage: Storage,
        alerts: AlertEngine,
    ):
        self.settings = settings
        self.monitor = monitor
        self.storage = storage
        self.alerts = alerts

    def _read_new(self, path: Path, cursor: int) -> bytes:
        with path.open("rb") as fh:
            fh.seek(cursor)
            return fh.read()

    def scan(self) -> list[AlertEvent]:
        events: list[AlertEvent] = []

        for name, _ in self.settings.log_files:
            key = f"logerrors:{name}"
            try:
                path = self.monitor.log_file_path(name)
            except ValueError as exc:
                logger.warning("App log %s: %s", name, exc)
                continue

            if not path.exists():
                event = self.alerts.transition(
                    key, "critical", f"App log {name}", f"File missing: {path}"
                )
                if event:
                    events.append(event)
                continue

            try:
                size = path.stat().st_size
            except OSError as exc:
                event = self.alerts.transition(
                    key, "critical", f"App log {name}", f"Cannot stat: {exc}"
                )
                if event:
                    events.append(event)
                continue

            cursor_raw = self.storage.get_meta(CURSOR_PREFIX + name)
            if cursor_raw is None:
                # First observation: start from the end, alert on nothing.
                self.storage.set_meta(CURSOR_PREFIX + name, str(size))
                continue

            try:
                cursor = int(cursor_raw)
            except ValueError:
                cursor = 0
            if size < cursor:
                # File was rotated or truncated.
                cursor = 0

            try:
                new_bytes = self._read_new(path, cursor)
            except OSError as exc:
                event = self.alerts.transition(
                    key, "critical", f"App log {name}", f"Cannot read: {exc}"
                )
                if event:
                    events.append(event)
                continue

            matches = 0
            sample = ""
            for line in new_bytes.decode("utf-8", errors="replace").splitlines():
                if any(p in line for p in self.settings.log_error_patterns):
                    matches += 1
                    sample = line.strip()[:300]

            self.storage.set_meta(CURSOR_PREFIX + name, str(size))

            if matches >= self.settings.log_error_threshold:
                level = "critical"
                details = (
                    f"New error lines: {matches} "
                    f"(threshold {self.settings.log_error_threshold})"
                    + (f"\n{sample}" if sample else "")
                )
            else:
                level = "ok"
                details = f"No new error lines ({matches} matched)"

            event = self.alerts.transition(
                key, level, f"App log {name}", details
            )
            if event:
                events.append(event)

        return events
