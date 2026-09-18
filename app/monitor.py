from __future__ import annotations

import os
import platform
import shutil
import socket
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import psutil

from .config import Settings
from .utils import human_bytes, human_duration, run_command


class ServerMonitor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone)

    # ---------- Core system ----------

    def boot_id(self) -> str:
        path = Path("/proc/sys/kernel/random/boot_id")
        if path.exists():
            return path.read_text().strip()
        return str(int(psutil.boot_time()))

    def boot_time_text(self) -> str:
        return datetime.fromtimestamp(
            psutil.boot_time(), tz=self.tz
        ).strftime("%Y-%m-%d %H:%M:%S %Z")

    def cpu(self) -> dict:
        usage = psutil.cpu_percent(interval=0.35)
        per_cpu = psutil.cpu_percent(interval=None, percpu=True)
        cpu_times = psutil.cpu_times_percent(interval=None)
        iowait = float(getattr(cpu_times, "iowait", 0.0))
        core_count = psutil.cpu_count(logical=True) or 1
        physical = psutil.cpu_count(logical=False) or core_count
        load1, load5, load15 = os.getloadavg()
        return {
            "usage": usage,
            "per_cpu": per_cpu,
            "iowait": iowait,
            "logical_cores": core_count,
            "physical_cores": physical,
            "load1": load1,
            "load5": load5,
            "load15": load15,
            "load_ratio_1m": load1 / core_count,
        }

    def memory(self) -> dict:
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        return {
            "total": mem.total,
            "available": mem.available,
            "used": mem.used,
            "percent": mem.percent,
            "swap_total": swap.total,
            "swap_used": swap.used,
            "swap_percent": swap.percent,
        }

    def filesystems(self) -> list[dict]:
        rows = []
        for mount in self.settings.disk_mounts:
            try:
                usage = psutil.disk_usage(mount)
                stat = os.statvfs(mount)
                inode_total = stat.f_files
                inode_free = stat.f_ffree
                inode_used = max(0, inode_total - inode_free)
                inode_percent = (
                    (inode_used / inode_total * 100) if inode_total else 0.0
                )
                rows.append(
                    {
                        "mount": mount,
                        "total": usage.total,
                        "used": usage.used,
                        "free": usage.free,
                        "percent": usage.percent,
                        "inode_total": inode_total,
                        "inode_used": inode_used,
                        "inode_percent": inode_percent,
                        "error": None,
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "mount": mount,
                        "error": str(exc),
                    }
                )
        return rows

    def disk_io(self) -> dict:
        io = psutil.disk_io_counters()
        if io is None:
            return {}
        return {
            "read_bytes": io.read_bytes,
            "write_bytes": io.write_bytes,
            "read_count": io.read_count,
            "write_count": io.write_count,
            "read_time_ms": io.read_time,
            "write_time_ms": io.write_time,
        }

    def uptime(self) -> dict:
        boot = psutil.boot_time()
        return {
            "boot_timestamp": boot,
            "boot_text": self.boot_time_text(),
            "uptime_seconds": time.time() - boot,
            "boot_id": self.boot_id(),
        }

    def system_info(self) -> dict:
        uname = platform.uname()
        return {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "system": uname.system,
            "release": uname.release,
            "machine": uname.machine,
            "python": platform.python_version(),
        }

    # ---------- Processes ----------

    def processes(self) -> dict:
        total = 0
        zombies = 0
        statuses: dict[str, int] = {}
        for proc in psutil.process_iter(["status"]):
            total += 1
            try:
                status = proc.info["status"] or "unknown"
                statuses[status] = statuses.get(status, 0) + 1
                if status == psutil.STATUS_ZOMBIE:
                    zombies += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return {"total": total, "zombies": zombies, "statuses": statuses}

    def top_processes(self, by: str = "memory", limit: int = 10) -> list[dict]:
        if by not in {"memory", "cpu"}:
            raise ValueError("by must be memory or cpu")

        processes = list(psutil.process_iter(["pid", "name", "username"]))
        if by == "cpu":
            for proc in processes:
                try:
                    proc.cpu_percent(None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            time.sleep(0.25)

        rows = []
        for proc in processes:
            try:
                rows.append(
                    {
                        "pid": proc.pid,
                        "name": proc.name() or "?",
                        "username": proc.username() or "?",
                        "memory_percent": float(proc.memory_percent()),
                        "cpu_percent": float(proc.cpu_percent(None)),
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        key = "memory_percent" if by == "memory" else "cpu_percent"
        rows.sort(key=lambda row: row[key], reverse=True)
        return rows[:limit]

    def disk_io_rates(self, interval: float = 0.5) -> dict:
        first = psutil.disk_io_counters()
        if first is None:
            return {}
        started = time.monotonic()
        time.sleep(interval)
        second = psutil.disk_io_counters()
        elapsed = max(time.monotonic() - started, 0.001)
        if second is None:
            return {}
        return {
            "read_bytes_per_sec": (second.read_bytes - first.read_bytes) / elapsed,
            "write_bytes_per_sec": (second.write_bytes - first.write_bytes) / elapsed,
            "reads_per_sec": (second.read_count - first.read_count) / elapsed,
            "writes_per_sec": (second.write_count - first.write_count) / elapsed,
        }

    # ---------- systemd ----------

    def service_status(self, service: str) -> dict:
        code, out, err = run_command(
            ["systemctl", "is-active", service],
            self.settings.command_timeout_seconds,
        )
        state = out.strip() or ("unknown" if code == 127 else "inactive")
        return {
            "service": service,
            "state": state,
            "active": code == 0 and state == "active",
            "error": err if code not in {0, 3} else "",
        }

    def services(self) -> list[dict]:
        return [
            self.service_status(service)
            for service in self.settings.monitored_services
        ]

    def failed_units(self) -> dict:
        code, out, err = run_command(
            [
                "systemctl",
                "--failed",
                "--no-legend",
                "--plain",
                "--no-pager",
            ],
            self.settings.command_timeout_seconds,
        )
        lines = [line for line in out.splitlines() if line.strip()]
        return {
            "available": code != 127,
            "count": len(lines),
            "lines": lines,
            "error": err,
        }

    # ---------- Docker ----------

    def docker_status(self) -> dict:
        code, out, err = run_command(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            self.settings.command_timeout_seconds,
        )
        return {
            "available": code != 127,
            "running": code == 0,
            "version": out.strip(),
            "error": err,
        }

    def containers(self) -> list[dict]:
        code, out, err = run_command(
            [
                "docker",
                "ps",
                "-a",
                "--format",
                "{{.Names}}\t{{.Status}}\t{{.State}}\t{{.Image}}",
            ],
            self.settings.command_timeout_seconds,
        )
        if code != 0:
            return []

        rows = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            name, status, state, image = parts[0], parts[1], parts[2], parts[3]
            lower_status = status.lower()
            health = "unknown"
            if "(healthy)" in lower_status:
                health = "healthy"
            elif "(unhealthy)" in lower_status:
                health = "unhealthy"
            rows.append(
                {
                    "name": name,
                    "status": status,
                    "state": state,
                    "image": image,
                    "health": health,
                }
            )
        return rows

    # ---------- Network ----------

    def network(self) -> dict:
        counters = psutil.net_io_counters()
        interfaces = []
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()

        for name, iface_stats in stats.items():
            addresses = []
            for addr in addrs.get(name, []):
                if addr.family in {socket.AF_INET, socket.AF_INET6}:
                    addresses.append(addr.address)
            interfaces.append(
                {
                    "name": name,
                    "up": iface_stats.isup,
                    "speed_mbps": iface_stats.speed,
                    "addresses": addresses,
                }
            )

        return {
            "bytes_sent": counters.bytes_sent,
            "bytes_recv": counters.bytes_recv,
            "packets_sent": counters.packets_sent,
            "packets_recv": counters.packets_recv,
            "errin": counters.errin,
            "errout": counters.errout,
            "dropin": counters.dropin,
            "dropout": counters.dropout,
            "interfaces": interfaces,
        }

    def tcp_check(self, host: str, port: int, timeout: float = 2.0) -> dict:
        started = time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                latency_ms = (time.monotonic() - started) * 1000
                return {
                    "host": host,
                    "port": port,
                    "open": True,
                    "latency_ms": latency_ms,
                    "error": "",
                }
        except Exception as exc:
            return {
                "host": host,
                "port": port,
                "open": False,
                "latency_ms": None,
                "error": str(exc),
            }

    def ports(self) -> list[dict]:
        rows = []
        for name, host, port in self.settings.monitored_ports:
            result = self.tcp_check(host, int(port))
            result["name"] = name
            rows.append(result)
        return rows

    # ---------- HTTP ----------

    def health_checks(self) -> list[dict]:
        rows = []
        with httpx.Client(
            timeout=self.settings.http_timeout_seconds,
            follow_redirects=True,
        ) as client:
            for name, url in self.settings.health_urls:
                started = time.monotonic()
                try:
                    response = client.get(url)
                    latency = (time.monotonic() - started) * 1000
                    rows.append(
                        {
                            "name": name,
                            "url": url,
                            "ok": 200 <= response.status_code < 400,
                            "status_code": response.status_code,
                            "latency_ms": latency,
                            "error": "",
                        }
                    )
                except Exception as exc:
                    rows.append(
                        {
                            "name": name,
                            "url": url,
                            "ok": False,
                            "status_code": None,
                            "latency_ms": None,
                            "error": str(exc),
                        }
                    )
        return rows

    # ---------- Database ----------

    def database(self) -> dict:
        if self.settings.db_type == "none":
            return {"configured": False}

        host = self.settings.db_host
        port = int(self.settings.db_port or 0)
        tcp = self.tcp_check(host, port)

        result = {
            "configured": True,
            "type": self.settings.db_type,
            "host": host,
            "port": port,
            "reachable": tcp["open"],
            "latency_ms": tcp["latency_ms"],
            "detail": "",
        }

        if not tcp["open"]:
            result["detail"] = tcp["error"]
            return result

        if self.settings.db_type == "postgres":
            args = ["pg_isready", "-h", host, "-p", str(port)]
            if self.settings.db_name:
                args += ["-d", self.settings.db_name]
            code, out, err = run_command(
                args, self.settings.command_timeout_seconds
            )
            if code != 127:
                result["reachable"] = code == 0
                result["detail"] = out or err

        return result

    # ---------- SSL ----------

    def ssl_certificates(self) -> list[dict]:
        rows = []
        for name, hostname, port_text in self.settings.ssl_targets:
            port = int(port_text)
            try:
                context = ssl.create_default_context()
                with socket.create_connection(
                    (hostname, port), timeout=self.settings.http_timeout_seconds
                ) as sock:
                    with context.wrap_socket(
                        sock, server_hostname=hostname
                    ) as tls:
                        cert = tls.getpeercert()
                expires_raw = cert["notAfter"]
                expires = datetime.strptime(
                    expires_raw, "%b %d %H:%M:%S %Y %Z"
                ).replace(tzinfo=timezone.utc)
                remaining = expires - datetime.now(timezone.utc)
                rows.append(
                    {
                        "name": name,
                        "hostname": hostname,
                        "port": port,
                        "ok": True,
                        "expires": expires,
                        "days_left": remaining.total_seconds() / 86400,
                        "error": "",
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "name": name,
                        "hostname": hostname,
                        "port": port,
                        "ok": False,
                        "expires": None,
                        "days_left": None,
                        "error": str(exc),
                    }
                )
        return rows

    # ---------- Backups ----------

    def backup_status(self) -> list[dict]:
        rows = []
        now = time.time()
        for name, raw_path, max_hours_text in self.settings.backup_targets:
            path = Path(raw_path)
            max_hours = float(max_hours_text)

            try:
                if not path.exists():
                    raise FileNotFoundError(f"{path} does not exist")

                candidate = path
                if path.is_dir():
                    files = [p for p in path.iterdir() if p.is_file()]
                    if not files:
                        raise FileNotFoundError(f"No files found in {path}")
                    candidate = max(files, key=lambda p: p.stat().st_mtime)

                stat = candidate.stat()
                age_hours = (now - stat.st_mtime) / 3600
                rows.append(
                    {
                        "name": name,
                        "path": str(candidate),
                        "ok": age_hours <= max_hours,
                        "age_hours": age_hours,
                        "max_hours": max_hours,
                        "size": stat.st_size,
                        "mtime": datetime.fromtimestamp(
                            stat.st_mtime, tz=self.tz
                        ),
                        "error": "",
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "name": name,
                        "path": raw_path,
                        "ok": False,
                        "age_hours": None,
                        "max_hours": max_hours,
                        "size": None,
                        "mtime": None,
                        "error": str(exc),
                    }
                )
        return rows

    # ---------- Logs/security ----------

    def service_logs(self, service: str, lines: int = 50) -> str:
        if service not in self.settings.log_services:
            allowed = ", ".join(self.settings.log_services) or "none"
            raise ValueError(f"Service not allowed. Allowed: {allowed}")

        code, out, err = run_command(
            [
                "journalctl",
                "-u",
                service,
                "-n",
                str(min(max(lines, 1), 100)),
                "--no-pager",
                "--output=short-iso",
            ],
            self.settings.command_timeout_seconds,
        )
        if code != 0 and not out:
            return err or "No log output"
        return out or "No log entries"

    def log_file_path(self, name: str) -> Path:
        """Resolve an allowlisted application log name to its path."""
        mapping = dict(self.settings.log_files)
        if name not in mapping:
            allowed = ", ".join(mapping) or "none"
            raise ValueError(f"Log file not allowed. Allowed: {allowed}")
        return Path(mapping[name])

    def log_file(self, name: str, lines: int = 50) -> str:
        path = self.log_file_path(name)
        if not path.exists():
            return f"Log file not found: {path}"
        code, out, err = run_command(
            [
                "tail",
                "-n",
                str(min(max(lines, 1), 200)),
                str(path),
            ],
            self.settings.command_timeout_seconds,
        )
        if code != 0 and not out:
            return err or "No log output"
        return out or "No log entries"

    def container_logs(self, name: str, lines: int = 50) -> str:
        if name not in self.settings.log_containers:
            allowed = ", ".join(self.settings.log_containers) or "none"
            raise ValueError(f"Container log not allowed. Allowed: {allowed}")
        code, out, err = run_command(
            [
                "docker",
                "logs",
                "--tail",
                str(min(max(lines, 1), 200)),
                name,
            ],
            self.settings.command_timeout_seconds,
        )
        text = (out or "") + (err or "")
        if not text.strip():
            return "No log output"
        return text.strip()

    def recent_errors(self) -> str:
        code, out, err = run_command(
            [
                "journalctl",
                "-p",
                "0..3",
                "--since",
                f"{self.settings.error_lookback_minutes} minutes ago",
                "--no-pager",
                "--output=short-iso",
                "-n",
                "100",
            ],
            self.settings.command_timeout_seconds,
        )
        return out or err or "No critical/error journal entries."

    def ssh_failures(self) -> dict:
        code, out, err = run_command(
            [
                "journalctl",
                "-u",
                "ssh",
                "-u",
                "sshd",
                "--since",
                f"{self.settings.ssh_lookback_minutes} minutes ago",
                "--no-pager",
                "-n",
                "300",
            ],
            self.settings.command_timeout_seconds,
        )
        text = out.lower()
        needles = (
            "failed password",
            "authentication failure",
            "invalid user",
            "failed publickey",
        )
        matching = [
            line
            for line in out.splitlines()
            if any(needle in line.lower() for needle in needles)
        ]
        return {
            "available": code != 127,
            "count": len(matching),
            "lines": matching[-30:],
            "error": err,
        }

    def pending_updates(self) -> dict:
        if shutil.which("apt"):
            code, out, err = run_command(
                ["apt", "list", "--upgradable"],
                max(self.settings.command_timeout_seconds, 15),
            )
            lines = [
                line for line in out.splitlines()
                if line.strip() and not line.lower().startswith("listing")
            ]
            security = [
                line for line in lines
                if "security" in line.lower()
            ]
            return {
                "manager": "apt",
                "count": len(lines),
                "security_count": len(security),
                "lines": lines[:30],
                "error": err if code not in {0, 100} else "",
            }

        if shutil.which("dnf"):
            code, out, err = run_command(
                ["dnf", "-q", "check-update"],
                max(self.settings.command_timeout_seconds, 15),
            )
            lines = [
                line for line in out.splitlines()
                if line.strip() and not line.startswith("Last metadata")
            ]
            return {
                "manager": "dnf",
                "count": len(lines),
                "security_count": None,
                "lines": lines[:30],
                "error": err if code not in {0, 100} else "",
            }

        return {
            "manager": "unknown",
            "count": None,
            "security_count": None,
            "lines": [],
            "error": "No supported package manager found",
        }

    def reboot_required(self) -> dict:
        path = Path("/var/run/reboot-required")
        if path.exists():
            reason = path.read_text(errors="replace").strip()
            return {"required": True, "reason": reason}
        return {"required": False, "reason": ""}

    # ---------- Full snapshots ----------

    def fast_snapshot(self) -> dict:
        return {
            "system": self.system_info(),
            "cpu": self.cpu(),
            "memory": self.memory(),
            "filesystems": self.filesystems(),
            "services": self.services(),
            "docker": self.docker_status(),
            "containers": self.containers(),
            "health": self.health_checks(),
            "ports": self.ports(),
            "database": self.database(),
            "uptime": self.uptime(),
            "timestamp": datetime.now(self.tz),
        }

    def slow_snapshot(self) -> dict:
        return {
            "ssl": self.ssl_certificates(),
            "backups": self.backup_status(),
            "timestamp": datetime.now(self.tz),
        }
