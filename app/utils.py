from __future__ import annotations

import asyncio
import shutil
import subprocess
from datetime import timedelta
from typing import Sequence


def human_bytes(value: int | float) -> str:
    value = float(value)
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    return f"{value:.1f} {units[index]}"


def human_duration(seconds: int | float) -> str:
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def status_icon(level: str) -> str:
    return {
        "healthy": "🟢",
        "warning": "🟡",
        "critical": "🔴",
        "unknown": "⚪",
        "active": "🟢",
        "inactive": "🔴",
        "open": "🟢",
        "closed": "🔴",
    }.get(level.lower(), "⚪")


def run_command(
    args: Sequence[str],
    timeout: int = 8,
) -> tuple[int, str, str]:
    if not args:
        return 127, "", "No command"
    executable = shutil.which(args[0])
    if not executable:
        return 127, "", f"{args[0]} not installed"
    try:
        completed = subprocess.run(
            [executable, *args[1:]],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        )
        return (
            completed.returncode,
            completed.stdout.strip(),
            completed.stderr.strip(),
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"{args[0]} timed out after {timeout}s"
    except Exception as exc:
        return 1, "", str(exc)


async def run_blocking(func, /, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


def chunk_text(text: str, size: int = 3800) -> list[str]:
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > size and current:
            chunks.append(current.rstrip())
            current = ""
        if len(line) > size:
            if current:
                chunks.append(current.rstrip())
                current = ""
            for i in range(0, len(line), size):
                chunks.append(line[i:i + size].rstrip())
        else:
            current += line
    if current:
        chunks.append(current.rstrip())
    return chunks
