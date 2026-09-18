#!/usr/bin/env python3
"""Read-only snapshot agent for the multi-VM Telegram monitor.

Run on a monitored VM (no Telegram token required). It prints a single JSON
line to stdout describing a snapshot or the result of one collector method.
The central bot invokes it over SSH.

    agent.py snapshot fast
    agent.py snapshot slow
    agent.py call cpu
    agent.py call log_file '["api", 100]'
"""
from __future__ import annotations

import json
import logging
import sys

from app.config import Settings
from app.fleet import dumps
from app.monitor import ServerMonitor

logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

ALLOWED_METHODS = {
    "boot_id",
    "boot_time_text",
    "cpu",
    "memory",
    "filesystems",
    "disk_io",
    "uptime",
    "system_info",
    "processes",
    "top_processes",
    "disk_io_rates",
    "service_status",
    "services",
    "failed_units",
    "docker_status",
    "containers",
    "network",
    "tcp_check",
    "ports",
    "health_checks",
    "database",
    "ssl_certificates",
    "backup_status",
    "service_logs",
    "log_file",
    "container_logs",
    "recent_errors",
    "ssh_failures",
    "pending_updates",
    "reboot_required",
    "fast_snapshot",
    "slow_snapshot",
}


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: agent.py snapshot <fast|slow> | call <method> [json-args]",
            file=sys.stderr,
        )
        return 2

    settings = Settings.from_env()
    monitor = ServerMonitor(settings)
    mode = argv[1]

    if mode == "snapshot":
        kind = argv[2] if len(argv) > 2 else "fast"
        if kind == "slow":
            result = monitor.slow_snapshot()
        else:
            result = monitor.fast_snapshot()
    elif mode == "call":
        if len(argv) < 3:
            print("call requires a method name", file=sys.stderr)
            return 2
        method = argv[2]
        if method not in ALLOWED_METHODS:
            print(f"method not allowed: {method}", file=sys.stderr)
            return 3
        args = json.loads(argv[3]) if len(argv) > 3 else []
        if not isinstance(args, list):
            print("json-args must be a JSON array", file=sys.stderr)
            return 2
        result = getattr(monitor, method)(*args)
    else:
        print(f"unknown mode: {mode}", file=sys.stderr)
        return 2

    sys.stdout.write(dumps(result))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
