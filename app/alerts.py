from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .storage import Storage


@dataclass(slots=True)
class AlertEvent:
    text: str
    level: str
    key: str


class AlertEngine:
    def __init__(self, settings: Settings, storage: Storage):
        self.settings = settings
        self.storage = storage
        self._candidate: dict[str, tuple[str, int]] = {}

    def _counter(self, key: str, desired: str) -> int:
        previous = self._candidate.get(key)
        if previous and previous[0] == desired:
            count = previous[1] + 1
        else:
            count = 1
        self._candidate[key] = (desired, count)
        return count

    def transition(
        self,
        key: str,
        desired: str,
        title: str,
        details: str,
    ) -> AlertEvent | None:
        active = self.storage.get_active_alert(key)

        if desired == "ok":
            if not active:
                self._candidate.pop(key, None)
                return None

            count = self._counter(key, "ok")
            if count < self.settings.alert_recovery_count:
                return None

            previous_level = active["level"]
            self.storage.resolve_alert(key)
            self._candidate.pop(key, None)
            return AlertEvent(
                key=key,
                level="recovered",
                text=(
                    f"RECOVERED\n\n"
                    f"Server: {self.settings.server_name}\n"
                    f"Check: {title}\n"
                    f"Previous: {previous_level.upper()}\n"
                    f"{details}"
                ),
            )

        count = self._counter(key, desired)
        if count < self.settings.alert_breach_count:
            return None

        if active:
            if active["level"] == desired:
                self.storage.touch_alert(key, details)
                self._candidate.pop(key, None)
                return None

            self.storage.upsert_active_alert(
                key, desired, title, details
            )
            self._candidate.pop(key, None)
            return AlertEvent(
                key=key,
                level=desired,
                text=(
                    f"ALERT UPDATED — {desired.upper()}\n\n"
                    f"Server: {self.settings.server_name}\n"
                    f"Check: {title}\n"
                    f"{details}"
                ),
            )

        self.storage.upsert_active_alert(
            key, desired, title, details
        )
        self._candidate.pop(key, None)
        return AlertEvent(
            key=key,
            level=desired,
            text=(
                f"SERVER ALERT — {desired.upper()}\n\n"
                f"Server: {self.settings.server_name}\n"
                f"Check: {title}\n"
                f"{details}"
            ),
        )

    @staticmethod
    def level_for(
        value: float,
        warning: float,
        critical: float,
    ) -> str:
        if value >= critical:
            return "critical"
        if value >= warning:
            return "warning"
        return "ok"

    def evaluate_fast(self, snapshot: dict) -> list[AlertEvent]:
        s = self.settings
        events: list[AlertEvent] = []

        def emit(
            key: str,
            level: str,
            title: str,
            details: str,
        ) -> None:
            event = self.transition(key, level, title, details)
            if event:
                events.append(event)

        cpu = snapshot["cpu"]
        emit(
            "cpu",
            self.level_for(cpu["usage"], s.cpu_warning, s.cpu_critical),
            "CPU usage",
            f"CPU: {cpu['usage']:.1f}%",
        )
        emit(
            "iowait",
            self.level_for(
                cpu["iowait"], s.iowait_warning, s.iowait_critical
            ),
            "CPU I/O wait",
            f"I/O wait: {cpu['iowait']:.1f}%",
        )

        load_warn = s.load_warning_multiplier
        load_crit = s.load_critical_multiplier
        ratio = cpu["load_ratio_1m"]
        emit(
            "load",
            self.level_for(ratio, load_warn, load_crit),
            "1-minute load",
            (
                f"Load: {cpu['load1']:.2f} across "
                f"{cpu['logical_cores']} logical CPUs "
                f"(ratio {ratio:.2f})"
            ),
        )

        mem = snapshot["memory"]
        emit(
            "memory",
            self.level_for(
                mem["percent"], s.memory_warning, s.memory_critical
            ),
            "Memory usage",
            f"Memory: {mem['percent']:.1f}%",
        )
        if mem["swap_total"] > 0:
            emit(
                "swap",
                self.level_for(
                    mem["swap_percent"], s.swap_warning, s.swap_critical
                ),
                "Swap usage",
                f"Swap: {mem['swap_percent']:.1f}%",
            )

        for fs in snapshot["filesystems"]:
            mount = fs["mount"]
            if fs.get("error"):
                emit(
                    f"disk:{mount}",
                    "critical",
                    f"Filesystem {mount}",
                    fs["error"],
                )
                continue

            emit(
                f"disk:{mount}",
                self.level_for(
                    fs["percent"], s.disk_warning, s.disk_critical
                ),
                f"Disk usage {mount}",
                f"Disk: {fs['percent']:.1f}%",
            )
            emit(
                f"inode:{mount}",
                self.level_for(
                    fs["inode_percent"],
                    s.inode_warning,
                    s.inode_critical,
                ),
                f"Inode usage {mount}",
                f"Inodes: {fs['inode_percent']:.1f}%",
            )

        for service in snapshot["services"]:
            if service["state"] == "unknown":
                continue
            emit(
                f"service:{service['service']}",
                "ok" if service["active"] else "critical",
                f"Service {service['service']}",
                f"State: {service['state']}",
            )

        configured_containers = set(s.monitored_containers)
        if configured_containers:
            found = {row["name"]: row for row in snapshot["containers"]}
            for name in configured_containers:
                row = found.get(name)
                healthy = bool(
                    row
                    and row["state"] == "running"
                    and row["health"] != "unhealthy"
                )
                detail = (
                    f"State: {row['state']}; {row['status']}"
                    if row else "Container not found"
                )
                emit(
                    f"container:{name}",
                    "ok" if healthy else "critical",
                    f"Container {name}",
                    detail,
                )

        for health in snapshot["health"]:
            emit(
                f"http:{health['name']}",
                "ok" if health["ok"] else "critical",
                f"HTTP health {health['name']}",
                (
                    f"HTTP {health['status_code']} "
                    f"in {health['latency_ms']:.0f} ms"
                    if health["latency_ms"] is not None
                    else health["error"]
                ),
            )

        for port in snapshot["ports"]:
            emit(
                f"port:{port['name']}",
                "ok" if port["open"] else "critical",
                f"TCP port {port['name']}",
                (
                    f"{port['host']}:{port['port']} reachable"
                    if port["open"]
                    else f"{port['host']}:{port['port']} unavailable: "
                         f"{port['error']}"
                ),
            )

        db = snapshot["database"]
        if db.get("configured"):
            emit(
                "database",
                "ok" if db["reachable"] else "critical",
                "Database",
                (
                    f"{db['type']} {db['host']}:{db['port']} reachable"
                    if db["reachable"]
                    else f"{db['type']} unreachable: {db['detail']}"
                ),
            )

        return events

    def evaluate_slow(self, snapshot: dict) -> list[AlertEvent]:
        events: list[AlertEvent] = []

        def emit(key: str, level: str, title: str, details: str) -> None:
            event = self.transition(key, level, title, details)
            if event:
                events.append(event)

        for cert in snapshot["ssl"]:
            if not cert["ok"]:
                level = "critical"
                details = cert["error"]
            else:
                days = cert["days_left"]
                if days < 7:
                    level = "critical"
                elif days < 30:
                    level = "warning"
                else:
                    level = "ok"
                details = f"Expires in {days:.1f} days"

            emit(
                f"ssl:{cert['name']}",
                level,
                f"TLS certificate {cert['name']}",
                details,
            )

        for backup in snapshot["backups"]:
            emit(
                f"backup:{backup['name']}",
                "ok" if backup["ok"] else "critical",
                f"Backup {backup['name']}",
                (
                    f"Age {backup['age_hours']:.1f}h "
                    f"(maximum {backup['max_hours']:.1f}h)"
                    if backup["age_hours"] is not None
                    else backup["error"]
                ),
            )

        return events
