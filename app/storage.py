from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    alert_key TEXT PRIMARY KEY,
                    level TEXT NOT NULL,
                    title TEXT NOT NULL,
                    details TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def get_active_alert(self, key: str):
        with self._lock, self._connect() as conn:
            return conn.execute(
                "SELECT * FROM alerts WHERE alert_key = ? AND active = 1",
                (key,),
            ).fetchone()

    def upsert_active_alert(
        self, key: str, level: str, title: str, details: str
    ) -> None:
        now = utc_now()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT first_seen, active FROM alerts WHERE alert_key = ?",
                (key,),
            ).fetchone()
            first_seen = (
                existing["first_seen"]
                if existing and existing["active"] == 1
                else now
            )
            conn.execute(
                """
                INSERT INTO alerts
                    (alert_key, level, title, details, first_seen, last_seen, active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(alert_key) DO UPDATE SET
                    level = excluded.level,
                    title = excluded.title,
                    details = excluded.details,
                    last_seen = excluded.last_seen,
                    active = 1
                """,
                (key, level, title, details, first_seen, now),
            )

    def touch_alert(self, key: str, details: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE alerts
                SET details = ?, last_seen = ?
                WHERE alert_key = ? AND active = 1
                """,
                (details, utc_now(), key),
            )

    def resolve_alert(self, key: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE alerts
                SET active = 0, last_seen = ?
                WHERE alert_key = ?
                """,
                (utc_now(), key),
            )

    def active_alerts(self):
        with self._lock, self._connect() as conn:
            return conn.execute(
                """
                SELECT * FROM alerts
                WHERE active = 1
                ORDER BY
                    CASE level WHEN 'critical' THEN 0 ELSE 1 END,
                    first_seen ASC
                """
            ).fetchall()

    def get_meta(self, key: str) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO meta(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
