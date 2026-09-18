from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from dotenv import load_dotenv


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _int_set(value: str | None) -> set[int]:
    result: set[int] = set()
    for item in _csv(value):
        result.add(int(item))
    return result


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _pairs(value: str | None, parts: int) -> list[tuple[str, ...]]:
    result: list[tuple[str, ...]] = []
    for item in _csv(value):
        fields = tuple(part.strip() for part in item.split("|"))
        if len(fields) != parts or any(not p for p in fields):
            raise ValueError(f"Invalid configuration item: {item!r}")
        result.append(fields)
    return result


@dataclass(slots=True)
class Settings:
    telegram_bot_token: str
    allowed_user_ids: set[int]
    alert_chat_id: int | None

    server_name: str
    timezone: str
    check_interval_seconds: int
    slow_check_interval_seconds: int
    command_timeout_seconds: int
    http_timeout_seconds: int
    alert_breach_count: int
    alert_recovery_count: int
    state_db: Path

    cpu_warning: float
    cpu_critical: float
    memory_warning: float
    memory_critical: float
    swap_warning: float
    swap_critical: float
    disk_warning: float
    disk_critical: float
    inode_warning: float
    inode_critical: float
    iowait_warning: float
    iowait_critical: float
    load_warning_multiplier: float
    load_critical_multiplier: float

    disk_mounts: list[str] = field(default_factory=list)
    monitored_services: list[str] = field(default_factory=list)
    log_services: list[str] = field(default_factory=list)
    monitored_containers: list[str] = field(default_factory=list)
    log_files: list[tuple[str, str]] = field(default_factory=list)
    log_containers: list[str] = field(default_factory=list)
    log_error_patterns: list[str] = field(default_factory=list)
    log_error_threshold: int = 5
    log_check_interval_seconds: int = 300

    health_urls: list[tuple[str, str]] = field(default_factory=list)
    monitored_ports: list[tuple[str, str, str]] = field(default_factory=list)

    db_type: str = "none"
    db_host: str = "127.0.0.1"
    db_port: int | None = None
    db_name: str = ""

    ssl_targets: list[tuple[str, str, str]] = field(default_factory=list)
    backup_targets: list[tuple[str, str, str]] = field(default_factory=list)

    error_lookback_minutes: int = 30
    ssh_lookback_minutes: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()

        raw_chat = os.getenv("ALERT_CHAT_ID", "").strip()
        db_port_raw = os.getenv("DB_PORT", "").strip()

        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            allowed_user_ids=_int_set(os.getenv("ALLOWED_USER_IDS")),
            alert_chat_id=int(raw_chat) if raw_chat else None,
            server_name=os.getenv("SERVER_NAME", "server").strip(),
            timezone=os.getenv("TIMEZONE", "Africa/Nairobi").strip(),
            check_interval_seconds=_int("CHECK_INTERVAL_SECONDS", 60),
            slow_check_interval_seconds=_int("SLOW_CHECK_INTERVAL_SECONDS", 3600),
            command_timeout_seconds=_int("COMMAND_TIMEOUT_SECONDS", 8),
            http_timeout_seconds=_int("HTTP_TIMEOUT_SECONDS", 5),
            alert_breach_count=_int("ALERT_BREACH_COUNT", 3),
            alert_recovery_count=_int("ALERT_RECOVERY_COUNT", 2),
            state_db=Path(os.getenv("STATE_DB", "./data/server_monitor.sqlite3")),
            cpu_warning=_float("CPU_WARNING", 85),
            cpu_critical=_float("CPU_CRITICAL", 95),
            memory_warning=_float("MEMORY_WARNING", 85),
            memory_critical=_float("MEMORY_CRITICAL", 95),
            swap_warning=_float("SWAP_WARNING", 20),
            swap_critical=_float("SWAP_CRITICAL", 50),
            disk_warning=_float("DISK_WARNING", 80),
            disk_critical=_float("DISK_CRITICAL", 90),
            inode_warning=_float("INODE_WARNING", 80),
            inode_critical=_float("INODE_CRITICAL", 90),
            iowait_warning=_float("IOWAIT_WARNING", 15),
            iowait_critical=_float("IOWAIT_CRITICAL", 25),
            load_warning_multiplier=_float("LOAD_WARNING_MULTIPLIER", 1.0),
            load_critical_multiplier=_float("LOAD_CRITICAL_MULTIPLIER", 1.5),
            disk_mounts=_csv(os.getenv("DISK_MOUNTS", "/")),
            monitored_services=_csv(os.getenv("MONITORED_SERVICES")),
            log_services=_csv(os.getenv("LOG_SERVICES")),
            monitored_containers=_csv(os.getenv("MONITORED_CONTAINERS")),
            log_files=[(a, b) for a, b in _pairs(os.getenv("LOG_FILES"), 2)],
            log_containers=_csv(os.getenv("LOG_CONTAINERS")),
            log_error_patterns=(
                _csv(os.getenv("LOG_ERROR_PATTERNS"))
                or [".ERROR", ".CRITICAL", ".ALERT", ".EMERGENCY"]
            ),
            log_error_threshold=_int("LOG_ERROR_THRESHOLD", 5),
            log_check_interval_seconds=_int("LOG_CHECK_INTERVAL_SECONDS", 300),
            health_urls=[(a, b) for a, b in _pairs(os.getenv("HEALTH_URLS"), 2)],
            monitored_ports=[
                (a, b, c) for a, b, c in _pairs(os.getenv("MONITORED_PORTS"), 3)
            ],
            db_type=os.getenv("DB_TYPE", "none").strip().lower(),
            db_host=os.getenv("DB_HOST", "127.0.0.1").strip(),
            db_port=int(db_port_raw) if db_port_raw else None,
            db_name=os.getenv("DB_NAME", "").strip(),
            ssl_targets=[
                (a, b, c) for a, b, c in _pairs(os.getenv("SSL_TARGETS"), 3)
            ],
            backup_targets=[
                (a, b, c) for a, b, c in _pairs(os.getenv("BACKUP_TARGETS"), 3)
            ],
            error_lookback_minutes=_int("ERROR_LOOKBACK_MINUTES", 30),
            ssh_lookback_minutes=_int("SSH_LOOKBACK_MINUTES", 60),
        )

    def validate(self) -> None:
        if not self.telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required")

        if self.check_interval_seconds < 15:
            raise ValueError("CHECK_INTERVAL_SECONDS must be at least 15")

        if self.slow_check_interval_seconds < 300:
            raise ValueError("SLOW_CHECK_INTERVAL_SECONDS must be at least 300")

        if self.alert_breach_count < 1 or self.alert_recovery_count < 1:
            raise ValueError("Alert counts must be at least 1")

        if self.log_check_interval_seconds < 60:
            raise ValueError("LOG_CHECK_INTERVAL_SECONDS must be at least 60")

        for warning, critical, name in [
            (self.cpu_warning, self.cpu_critical, "CPU"),
            (self.memory_warning, self.memory_critical, "MEMORY"),
            (self.swap_warning, self.swap_critical, "SWAP"),
            (self.disk_warning, self.disk_critical, "DISK"),
            (self.inode_warning, self.inode_critical, "INODE"),
            (self.iowait_warning, self.iowait_critical, "IOWAIT"),
        ]:
            if warning >= critical:
                raise ValueError(f"{name} warning must be below critical")

        if self.db_type not in {"none", "postgres", "mysql", "tcp"}:
            raise ValueError("DB_TYPE must be one of: none, postgres, mysql, tcp")

        if self.db_type != "none" and not self.db_port:
            default_ports = {"postgres": 5432, "mysql": 3306}
            self.db_port = default_ports.get(self.db_type)
            if not self.db_port:
                raise ValueError("DB_PORT is required for DB_TYPE=tcp")

        for _, _, port in self.monitored_ports:
            int(port)
        for _, _, port in self.ssl_targets:
            int(port)
        for _, _, hours in self.backup_targets:
            float(hours)

        self.state_db.parent.mkdir(parents=True, exist_ok=True)
