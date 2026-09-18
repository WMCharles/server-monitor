from __future__ import annotations

import functools
from datetime import datetime

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from . import __version__
from .config import Settings
from .monitor import ServerMonitor
from .storage import Storage
from .utils import (
    chunk_text,
    human_bytes,
    human_duration,
    run_blocking,
    status_icon,
)


def restricted(func):
    @functools.wraps(func)
    async def wrapper(
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *args,
        **kwargs,
    ):
        settings: Settings = context.application.bot_data["settings"]
        user = update.effective_user

        if user is None:
            return

        if not settings.allowed_user_ids:
            await update.effective_message.reply_text(
                "Bot access is not configured.\n"
                "Use /whoami, then add your numeric user ID to "
                "ALLOWED_USER_IDS in .env and restart the service."
            )
            return

        if user.id not in settings.allowed_user_ids:
            await update.effective_message.reply_text("Unauthorized.")
            return

        return await func(update, context, *args, **kwargs)

    return wrapper


class _SettingsView:
    """Settings proxy that reports the active server's display name."""

    def __init__(self, base, server_name: str):
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_server_name", server_name)

    def __getattr__(self, item):
        return getattr(object.__getattribute__(self, "_base"), item)

    @property
    def server_name(self) -> str:
        return object.__getattribute__(self, "_server_name")


def monitor(context):
    fleet = context.application.bot_data.get("fleet")
    if fleet is not None:
        return fleet.active(context)
    return context.application.bot_data["monitor"]


def settings(context):
    base = context.application.bot_data["settings"]
    fleet = context.application.bot_data.get("fleet")
    if fleet is None:
        return base
    name = context.chat_data.get("server") or fleet.first()
    return _SettingsView(base, name)


async def reply_chunks(update: Update, text: str) -> None:
    for chunk in chunk_text(text):
        await update.effective_message.reply_text(chunk)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Server Monitor Bot\n\n"
        "Use /whoami to get your Telegram user ID.\n"
        "Use /help to see available commands."
    )


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    await update.effective_message.reply_text(
        f"User ID: {user.id if user else 'unknown'}\n"
        f"Chat ID: {chat.id if chat else 'unknown'}"
    )


@restricted
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "SERVER MONITOR COMMANDS\n\n"
        "/status - overall health\n"
        "/system - host, OS and kernel\n"
        "/cpu - CPU and I/O wait\n"
        "/load - load averages\n"
        "/memory - RAM usage\n"
        "/swap - swap usage\n"
        "/disk - filesystem usage\n"
        "/io - disk I/O rates\n"
        "/inode - inode usage\n"
        "/uptime - boot time and uptime\n"
        "/services - monitored services\n"
        "/failed - failed systemd units\n"
        "/processes - process counts\n"
        "/topcpu - top CPU processes\n"
        "/topmem - top memory processes\n"
        "/docker - Docker daemon\n"
        "/containers - container list\n"
        "/health - configured HTTP checks\n"
        "/ports - expected TCP ports\n"
        "/network - interfaces/counters\n"
        "/db - database reachability\n"
        "/logs <service|file|container> [lines] - approved logs\n"
        "/errors - recent error/critical journal entries\n"
        "/ssl - certificate expiry\n"
        "/backups - backup freshness\n"
        "/alerts - active automatic alerts\n"
        "/thresholds - configured thresholds\n"
        "/security - SSH/reboot security summary\n"
        "/sshfails - recent failed SSH attempts\n"
        "/updates - pending OS packages\n"
        "/rebootrequired - reboot flag\n"
        "/servers - list configured servers\n"
        "/server <name> - switch active server\n"
        "/whoami - Telegram IDs\n"
        "/version - bot version"
    )


def metric_level(value: float, warning: float, critical: float) -> str:
    if value >= critical:
        return "critical"
    if value >= warning:
        return "warning"
    return "healthy"


@restricted
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fleet = context.application.bot_data.get("fleet")
    if fleet is not None and context.args and context.args[0].lower() == "all":
        await fleet_status(update, context, fleet)
        return

    m = monitor(context)
    s = settings(context)
    snap = await run_blocking(m.fast_snapshot)

    cpu = snap["cpu"]
    mem = snap["memory"]

    levels = [
        metric_level(cpu["usage"], s.cpu_warning, s.cpu_critical),
        metric_level(mem["percent"], s.memory_warning, s.memory_critical),
    ]

    fs_lines = []
    for fs in snap["filesystems"]:
        if fs.get("error"):
            levels.append("critical")
            fs_lines.append(f"{fs['mount']}: ERROR {fs['error']}")
        else:
            level = metric_level(
                fs["percent"], s.disk_warning, s.disk_critical
            )
            levels.append(level)
            fs_lines.append(
                f"{status_icon(level)} {fs['mount']}: "
                f"{fs['percent']:.1f}%"
            )

    service_lines = []
    for row in snap["services"]:
        level = "healthy" if row["active"] else "critical"
        levels.append(level)
        service_lines.append(
            f"{status_icon(level)} {row['service']}: {row['state']}"
        )

    container_lines = []
    configured_containers = set(s.monitored_containers)
    if configured_containers:
        found = {row["name"]: row for row in snap["containers"]}
        for name in s.monitored_containers:
            row = found.get(name)
            good = bool(
                row
                and row["state"] == "running"
                and row["health"] != "unhealthy"
            )
            level = "healthy" if good else "critical"
            levels.append(level)
            detail = row["status"] if row else "not found"
            container_lines.append(
                f"{status_icon(level)} {name}: {detail}"
            )

    port_lines = []
    for row in snap["ports"]:
        level = "healthy" if row["open"] else "critical"
        levels.append(level)
        detail = (
            f"open {row['latency_ms']:.0f}ms"
            if row["latency_ms"] is not None
            else "closed"
        )
        port_lines.append(
            f"{status_icon(level)} {row['name']}: {detail}"
        )

    db_line = "Not configured"
    db = snap["database"]
    if db.get("configured"):
        db_level = "healthy" if db["reachable"] else "critical"
        levels.append(db_level)
        db_line = (
            f"{status_icon(db_level)} {db['type']} "
            f"{db['host']}:{db['port']} — "
            f"{'reachable' if db['reachable'] else 'unreachable'}"
        )

    health_lines = []
    for row in snap["health"]:
        level = "healthy" if row["ok"] else "critical"
        levels.append(level)
        detail = (
            f"HTTP {row['status_code']} {row['latency_ms']:.0f}ms"
            if row["latency_ms"] is not None
            else row["error"]
        )
        health_lines.append(
            f"{status_icon(level)} {row['name']}: {detail}"
        )

    overall = (
        "critical" if "critical" in levels
        else "warning" if "warning" in levels
        else "healthy"
    )

    text = (
        f"{status_icon(overall)} SERVER STATUS — {s.server_name}\n\n"
        f"Overall: {overall.upper()}\n"
        f"CPU: {cpu['usage']:.1f}%\n"
        f"Load: {cpu['load1']:.2f} / {cpu['load5']:.2f} / "
        f"{cpu['load15']:.2f}\n"
        f"RAM: {mem['percent']:.1f}% "
        f"({human_bytes(mem['used'])}/{human_bytes(mem['total'])})\n"
        f"Swap: {mem['swap_percent']:.1f}%\n"
        f"Uptime: {human_duration(snap['uptime']['uptime_seconds'])}\n\n"
        f"DISKS\n"
        + ("\n".join(fs_lines) if fs_lines else "Not configured")
        + "\n\nSERVICES\n"
        + ("\n".join(service_lines) if service_lines else "Not configured")
        + "\n\nCONTAINERS\n"
        + ("\n".join(container_lines) if container_lines else "Not configured")
        + "\n\nPORTS\n"
        + ("\n".join(port_lines) if port_lines else "Not configured")
        + "\n\nDATABASE\n"
        + db_line
        + "\n\nHTTP HEALTH\n"
        + ("\n".join(health_lines) if health_lines else "Not configured")
        + f"\n\nChecked: {snap['timestamp']:%Y-%m-%d %H:%M:%S %Z}"
    )
    await reply_chunks(update, text)


async def fleet_status(update: Update, context: ContextTypes.DEFAULT_TYPE, fleet) -> None:
    s = context.application.bot_data["settings"]
    lines = ["FLEET STATUS", ""]
    for name in fleet.names():
        m = fleet.get(name)
        try:
            snap = await run_blocking(m.fast_snapshot)
        except Exception as exc:
            lines.append(f"\U0001f534 {name}: unreachable — {exc}")
            continue

        cpu = snap["cpu"]
        mem = snap["memory"]
        levels = [
            metric_level(cpu["usage"], s.cpu_warning, s.cpu_critical),
            metric_level(mem["percent"], s.memory_warning, s.memory_critical),
        ]
        disk_bits = []
        for fs in snap["filesystems"]:
            if fs.get("error"):
                levels.append("critical")
                disk_bits.append(f"{fs['mount']}:ERR")
            else:
                levels.append(
                    metric_level(fs["percent"], s.disk_warning, s.disk_critical)
                )
                disk_bits.append(f"{fs['mount']}:{fs['percent']:.0f}%")
        for row in snap["services"]:
            levels.append("healthy" if row["active"] else "critical")
        for row in snap["ports"]:
            levels.append("healthy" if row["open"] else "critical")
        for row in snap["health"]:
            levels.append("healthy" if row["ok"] else "critical")
        db = snap["database"]
        if db.get("configured"):
            levels.append("healthy" if db["reachable"] else "critical")
        expected = set(snap.get("monitored_containers", []))
        if expected:
            found = {r["name"]: r for r in snap["containers"]}
            for cname in expected:
                row = found.get(cname)
                good = bool(
                    row
                    and row["state"] == "running"
                    and row["health"] != "unhealthy"
                )
                levels.append("healthy" if good else "critical")

        overall = (
            "critical" if "critical" in levels
            else "warning" if "warning" in levels
            else "healthy"
        )
        lines.append(
            f"{status_icon(overall)} {name}: CPU {cpu['usage']:.0f}% | "
            f"RAM {mem['percent']:.0f}% | "
            f"disk {', '.join(disk_bits) or 'n/a'}"
        )

    lines += ["", "Use /server <name>, then any command, for detail."]
    await reply_chunks(update, "\n".join(lines))


@restricted
async def servers_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fleet = context.application.bot_data.get("fleet")
    if fleet is None:
        await update.effective_message.reply_text(
            "Single-server mode (HOSTS_FILE not configured)."
        )
        return
    active = context.chat_data.get("server") or fleet.first()
    lines = ["SERVERS", ""]
    for name, target, _ in settings(context).servers:
        mark = "\u25b6" if name == active else " "
        where = "local" if target in ("", "local") else target
        lines.append(f"{mark} {name} ({where})")
    lines += ["", "Use /server <name> to switch."]
    await update.effective_message.reply_text("\n".join(lines))


@restricted
async def server_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    fleet = context.application.bot_data.get("fleet")
    if fleet is None:
        await update.effective_message.reply_text(
            "Single-server mode (HOSTS_FILE not configured)."
        )
        return
    if not context.args:
        await update.effective_message.reply_text(
            "Usage: /server <name>\nAvailable: " + ", ".join(fleet.names())
        )
        return
    name = context.args[0]
    if name not in fleet.names():
        await update.effective_message.reply_text(
            f"Unknown server. Available: {', '.join(fleet.names())}"
        )
        return
    context.chat_data["server"] = name
    await update.effective_message.reply_text(f"Active server set to {name}.")


@restricted
async def system_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).system_info)
    await update.effective_message.reply_text(
        f"SYSTEM — {settings(context).server_name}\n\n"
        f"Hostname: {data['hostname']}\n"
        f"OS: {data['platform']}\n"
        f"Kernel: {data['release']}\n"
        f"Architecture: {data['machine']}\n"
        f"Python: {data['python']}"
    )


@restricted
async def cpu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).cpu)
    s = settings(context)
    cpu_level = metric_level(
        data["usage"], s.cpu_warning, s.cpu_critical
    )
    io_level = metric_level(
        data["iowait"], s.iowait_warning, s.iowait_critical
    )
    await update.effective_message.reply_text(
        f"CPU — {s.server_name}\n\n"
        f"{status_icon(cpu_level)} Usage: {data['usage']:.1f}%\n"
        f"{status_icon(io_level)} I/O wait: {data['iowait']:.1f}%\n"
        f"Physical cores: {data['physical_cores']}\n"
        f"Logical cores: {data['logical_cores']}\n\n"
        f"Load 1m: {data['load1']:.2f}\n"
        f"Load 5m: {data['load5']:.2f}\n"
        f"Load 15m: {data['load15']:.2f}\n"
        f"1m/core ratio: {data['load_ratio_1m']:.2f}"
    )


@restricted
async def load_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).cpu)
    s = settings(context)
    ratio = data['load_ratio_1m']
    level = metric_level(
        ratio, s.load_warning_multiplier, s.load_critical_multiplier
    )
    await update.effective_message.reply_text(
        f"LOAD — {s.server_name}\n\n"
        f"{status_icon(level)} 1m: {data['load1']:.2f}\n"
        f"5m: {data['load5']:.2f}\n"
        f"15m: {data['load15']:.2f}\n"
        f"Logical CPUs: {data['logical_cores']}\n"
        f"1m/core ratio: {ratio:.2f}"
    )


@restricted
async def memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).memory)
    s = settings(context)
    ram_level = metric_level(
        data["percent"], s.memory_warning, s.memory_critical
    )
    swap_level = metric_level(
        data["swap_percent"], s.swap_warning, s.swap_critical
    )
    await update.effective_message.reply_text(
        f"MEMORY — {s.server_name}\n\n"
        f"{status_icon(ram_level)} RAM: {data['percent']:.1f}%\n"
        f"Used: {human_bytes(data['used'])}\n"
        f"Available: {human_bytes(data['available'])}\n"
        f"Total: {human_bytes(data['total'])}\n\n"
        f"{status_icon(swap_level)} Swap: {data['swap_percent']:.1f}%\n"
        f"Used: {human_bytes(data['swap_used'])}\n"
        f"Total: {human_bytes(data['swap_total'])}"
    )


@restricted
async def swap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).memory)
    s = settings(context)
    level = metric_level(
        data['swap_percent'], s.swap_warning, s.swap_critical
    )
    await update.effective_message.reply_text(
        f"SWAP — {s.server_name}\n\n"
        f"{status_icon(level)} Usage: {data['swap_percent']:.1f}%\n"
        f"Used: {human_bytes(data['swap_used'])}\n"
        f"Total: {human_bytes(data['swap_total'])}"
    )


@restricted
async def disk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await run_blocking(monitor(context).filesystems)
    s = settings(context)
    lines = [f"DISK — {s.server_name}", ""]
    for row in rows:
        if row.get("error"):
            lines.append(f"🔴 {row['mount']}: {row['error']}")
            continue
        level = metric_level(
            row["percent"], s.disk_warning, s.disk_critical
        )
        lines.append(
            f"{status_icon(level)} {row['mount']}: {row['percent']:.1f}%\n"
            f"   Used {human_bytes(row['used'])} / "
            f"{human_bytes(row['total'])}; Free {human_bytes(row['free'])}"
        )
    await reply_chunks(update, "\n".join(lines))


@restricted
async def io_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).disk_io_rates)
    if not data:
        await update.effective_message.reply_text("Disk I/O counters unavailable.")
        return
    await update.effective_message.reply_text(
        f"DISK I/O — {settings(context).server_name}\n\n"
        f"Read: {human_bytes(data['read_bytes_per_sec'])}/s\n"
        f"Write: {human_bytes(data['write_bytes_per_sec'])}/s\n"
        f"Read IOPS: {data['reads_per_sec']:.1f}/s\n"
        f"Write IOPS: {data['writes_per_sec']:.1f}/s"
    )


@restricted
async def inode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await run_blocking(monitor(context).filesystems)
    s = settings(context)
    lines = [f"INODES — {s.server_name}", ""]
    for row in rows:
        if row.get("error"):
            lines.append(f"🔴 {row['mount']}: {row['error']}")
            continue
        level = metric_level(
            row["inode_percent"], s.inode_warning, s.inode_critical
        )
        lines.append(
            f"{status_icon(level)} {row['mount']}: "
            f"{row['inode_percent']:.1f}% "
            f"({row['inode_used']}/{row['inode_total']})"
        )
    await update.effective_message.reply_text("\n".join(lines))


@restricted
async def uptime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).uptime)
    await update.effective_message.reply_text(
        f"UPTIME — {settings(context).server_name}\n\n"
        f"Uptime: {human_duration(data['uptime_seconds'])}\n"
        f"Boot time: {data['boot_text']}\n"
        f"Boot ID: {data['boot_id']}"
    )


@restricted
async def services(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await run_blocking(monitor(context).services)
    if not rows:
        await update.effective_message.reply_text(
            "No MONITORED_SERVICES configured."
        )
        return
    lines = ["SERVICES", ""]
    for row in rows:
        level = "active" if row["active"] else "inactive"
        lines.append(
            f"{status_icon(level)} {row['service']}: {row['state']}"
        )
    await update.effective_message.reply_text("\n".join(lines))


@restricted
async def failed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).failed_units)
    if not data["available"]:
        await update.effective_message.reply_text(
            "systemctl is not available on this host."
        )
        return
    text = (
        f"FAILED SYSTEMD UNITS: {data['count']}\n\n"
        + ("\n".join(data["lines"]) if data["lines"] else "None")
    )
    await reply_chunks(update, text)


@restricted
async def processes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).processes)
    statuses = ", ".join(
        f"{k}={v}" for k, v in sorted(data["statuses"].items())
    )
    await update.effective_message.reply_text(
        f"PROCESSES\n\n"
        f"Total: {data['total']}\n"
        f"Zombies: {data['zombies']}\n"
        f"States: {statuses or 'n/a'}"
    )


async def _top_processes(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    by: str,
) -> None:
    rows = await run_blocking(monitor(context).top_processes, by, 10)
    metric = "memory_percent" if by == "memory" else "cpu_percent"
    lines = [f"TOP {by.upper()} PROCESSES", ""]
    for row in rows:
        lines.append(
            f"{row['pid']:>6}  {row[metric]:>6.1f}%  "
            f"{row['name'][:28]}"
        )
    await update.effective_message.reply_text("\n".join(lines))


@restricted
async def topcpu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _top_processes(update, context, "cpu")


@restricted
async def topmem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _top_processes(update, context, "memory")


@restricted
async def docker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).docker_status)
    if not data["available"]:
        text = "Docker CLI is not installed."
    elif data["running"]:
        text = f"🟢 Docker running\nServer version: {data['version']}"
    else:
        text = f"🔴 Docker unavailable\n{data['error']}"
    await update.effective_message.reply_text(text)


@restricted
async def containers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await run_blocking(monitor(context).containers)
    if not rows:
        await update.effective_message.reply_text(
            "No containers found or Docker is unavailable."
        )
        return
    lines = ["CONTAINERS", ""]
    for row in rows:
        good = row["state"] == "running" and row["health"] != "unhealthy"
        lines.append(
            f"{'🟢' if good else '🔴'} {row['name']}\n"
            f"   {row['status']} | {row['image']}"
        )
    await reply_chunks(update, "\n".join(lines))


@restricted
async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await run_blocking(monitor(context).health_checks)
    if not rows:
        await update.effective_message.reply_text(
            "No HEALTH_URLS configured."
        )
        return
    lines = ["HTTP HEALTH", ""]
    for row in rows:
        if row["latency_ms"] is not None:
            detail = f"HTTP {row['status_code']} — {row['latency_ms']:.0f} ms"
        else:
            detail = row["error"]
        lines.append(f"{'🟢' if row['ok'] else '🔴'} {row['name']}: {detail}")
    await update.effective_message.reply_text("\n".join(lines))


@restricted
async def ports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await run_blocking(monitor(context).ports)
    if not rows:
        await update.effective_message.reply_text(
            "No MONITORED_PORTS configured."
        )
        return
    lines = ["PORTS", ""]
    for row in rows:
        if row["open"]:
            detail = f"OPEN — {row['latency_ms']:.0f} ms"
        else:
            detail = f"CLOSED — {row['error']}"
        lines.append(
            f"{'🟢' if row['open'] else '🔴'} {row['name']} "
            f"{row['host']}:{row['port']} — {detail}"
        )
    await reply_chunks(update, "\n".join(lines))


@restricted
async def network(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).network)
    lines = [
        "NETWORK",
        "",
        f"RX: {human_bytes(data['bytes_recv'])}",
        f"TX: {human_bytes(data['bytes_sent'])}",
        f"RX errors/drops: {data['errin']}/{data['dropin']}",
        f"TX errors/drops: {data['errout']}/{data['dropout']}",
        "",
        "INTERFACES",
    ]
    for row in data["interfaces"]:
        lines.append(
            f"{'🟢' if row['up'] else '🔴'} {row['name']} "
            f"{row['speed_mbps']}Mbps "
            f"{', '.join(row['addresses']) or '-'}"
        )
    await reply_chunks(update, "\n".join(lines))


@restricted
async def db(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).database)
    if not data.get("configured"):
        await update.effective_message.reply_text("Database check not configured.")
        return
    text = (
        f"{'🟢' if data['reachable'] else '🔴'} DATABASE\n\n"
        f"Type: {data['type']}\n"
        f"Target: {data['host']}:{data['port']}\n"
        f"Reachable: {'yes' if data['reachable'] else 'no'}\n"
        f"Latency: "
        + (f"{data['latency_ms']:.0f} ms" if data['latency_ms'] is not None else "-")
        + "\n"
        + f"Detail: {data['detail'] or '-'}"
    )
    await update.effective_message.reply_text(text)


@restricted
async def logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = monitor(context)
    s = settings(context)

    if not context.args:
        allowed = (
            list(s.log_services)
            + [name for name, _ in s.log_files]
            + list(s.log_containers)
        )
        await update.effective_message.reply_text(
            "Usage: /logs <target> [lines]\n"
            f"Allowed: {', '.join(allowed) or 'none'}"
        )
        return

    target = context.args[0]
    lines = 50
    if len(context.args) >= 2:
        try:
            lines = min(max(int(context.args[1]), 1), 200)
        except ValueError:
            await update.effective_message.reply_text("Lines must be a number.")
            return

    try:
        if target in s.log_services:
            text = await run_blocking(m.service_logs, target, lines)
        elif target in {name for name, _ in s.log_files}:
            text = await run_blocking(m.log_file, target, lines)
        elif target in s.log_containers:
            text = await run_blocking(m.container_logs, target, lines)
        else:
            allowed = (
                list(s.log_services)
                + [name for name, _ in s.log_files]
                + list(s.log_containers)
            )
            raise ValueError(
                f"Target not allowed. Allowed: {', '.join(allowed) or 'none'}"
            )
    except ValueError as exc:
        await update.effective_message.reply_text(str(exc))
        return

    await reply_chunks(update, f"LOGS — {target}\n\n{text}")


@restricted
async def errors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = await run_blocking(monitor(context).recent_errors)
    await reply_chunks(update, f"RECENT SYSTEM ERRORS\n\n{text}")


@restricted
async def ssl_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await run_blocking(monitor(context).ssl_certificates)
    if not rows:
        await update.effective_message.reply_text("No SSL_TARGETS configured.")
        return
    lines = ["TLS CERTIFICATES", ""]
    for row in rows:
        if row["ok"]:
            if row["days_left"] < 7:
                icon = "🔴"
            elif row["days_left"] < 30:
                icon = "🟡"
            else:
                icon = "🟢"
            lines.append(
                f"{icon} {row['name']} ({row['hostname']}:{row['port']})\n"
                f"   Expires: {row['expires']:%Y-%m-%d} "
                f"({row['days_left']:.1f} days)"
            )
        else:
            lines.append(f"🔴 {row['name']}: {row['error']}")
    await update.effective_message.reply_text("\n".join(lines))


@restricted
async def backups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = await run_blocking(monitor(context).backup_status)
    if not rows:
        await update.effective_message.reply_text(
            "No BACKUP_TARGETS configured."
        )
        return
    lines = ["BACKUPS", ""]
    for row in rows:
        if row["age_hours"] is not None:
            lines.append(
                f"{'🟢' if row['ok'] else '🔴'} {row['name']}: "
                f"{row['age_hours']:.1f}h old "
                f"(max {row['max_hours']:.1f}h)\n"
                f"   {row['path']} — {human_bytes(row['size'])}"
            )
        else:
            lines.append(f"🔴 {row['name']}: {row['error']}")
    await reply_chunks(update, "\n".join(lines))


@restricted
async def alerts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    storage: Storage = context.application.bot_data["storage"]
    rows = storage.active_alerts()
    if not rows:
        await update.effective_message.reply_text("No active alerts.")
        return

    lines = ["ACTIVE ALERTS", ""]
    for row in rows:
        icon = "🔴" if row["level"] == "critical" else "🟡"
        lines.append(
            f"{icon} {row['title']}\n"
            f"   Level: {row['level'].upper()}\n"
            f"   Since: {row['first_seen']}\n"
            f"   {row['details']}"
        )
    await reply_chunks(update, "\n".join(lines))


@restricted
async def thresholds(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = settings(context)
    await update.effective_message.reply_text(
        "THRESHOLDS\n\n"
        f"CPU: {s.cpu_warning:.0f}% / {s.cpu_critical:.0f}%\n"
        f"Memory: {s.memory_warning:.0f}% / {s.memory_critical:.0f}%\n"
        f"Swap: {s.swap_warning:.0f}% / {s.swap_critical:.0f}%\n"
        f"Disk: {s.disk_warning:.0f}% / {s.disk_critical:.0f}%\n"
        f"Inodes: {s.inode_warning:.0f}% / {s.inode_critical:.0f}%\n"
        f"I/O wait: {s.iowait_warning:.0f}% / {s.iowait_critical:.0f}%\n"
        f"Load/core: {s.load_warning_multiplier:.1f} / "
        f"{s.load_critical_multiplier:.1f}\n\n"
        f"Checks: every {s.check_interval_seconds}s\n"
        f"Slow checks: every {s.slow_check_interval_seconds}s\n"
        f"Alert after {s.alert_breach_count} consecutive breaches\n"
        f"Recover after {s.alert_recovery_count} healthy checks"
    )


@restricted
async def sshfails(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).ssh_failures)
    text = (
        f"FAILED SSH ATTEMPTS\n\n"
        f"Window: last {settings(context).ssh_lookback_minutes} minutes\n"
        f"Matches: {data['count']}\n\n"
        + ("\n".join(data["lines"]) if data["lines"] else "No matches")
    )
    await reply_chunks(update, text)


@restricted
async def updates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = await run_blocking(monitor(context).pending_updates)
    lines = [
        "PACKAGE UPDATES",
        "",
        f"Manager: {data['manager']}",
        f"Pending: {data['count'] if data['count'] is not None else 'unknown'}",
    ]
    if data["security_count"] is not None:
        lines.append(f"Security-labelled: {data['security_count']}")
    if data["lines"]:
        lines += ["", "First packages:", *data["lines"][:20]]
    if data["error"]:
        lines += ["", f"Note: {data['error']}"]
    await reply_chunks(update, "\n".join(lines))


@restricted
async def rebootrequired(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    data = await run_blocking(monitor(context).reboot_required)
    await update.effective_message.reply_text(
        (
            "🟡 Reboot required."
            + (f"\n{data['reason']}" if data["reason"] else "")
        )
        if data["required"]
        else "🟢 No reboot-required flag found."
    )


@restricted
async def security(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ssh = await run_blocking(monitor(context).ssh_failures)
    reboot = await run_blocking(monitor(context).reboot_required)
    await update.effective_message.reply_text(
        "SECURITY SUMMARY\n\n"
        f"Failed SSH matches "
        f"({settings(context).ssh_lookback_minutes}m): {ssh['count']}\n"
        f"Reboot required: {'YES' if reboot['required'] else 'no'}\n\n"
        "Use /updates for package update information."
    )


@restricted
async def version(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        f"Server Monitor Bot v{__version__}"
    )


def register_handlers(application: Application) -> None:
    handlers = [
        ("start", start),
        ("whoami", whoami),
        ("help", help_command),
        ("status", status),
        ("system", system_command),
        ("cpu", cpu),
        ("load", load_command),
        ("memory", memory),
        ("swap", swap),
        ("disk", disk),
        ("io", io_command),
        ("inode", inode),
        ("uptime", uptime),
        ("services", services),
        ("failed", failed),
        ("processes", processes),
        ("topcpu", topcpu),
        ("topmem", topmem),
        ("docker", docker),
        ("containers", containers),
        ("health", health),
        ("ports", ports),
        ("network", network),
        ("db", db),
        ("logs", logs),
        ("errors", errors),
        ("ssl", ssl_command),
        ("backups", backups),
        ("alerts", alerts),
        ("thresholds", thresholds),
        ("security", security),
        ("sshfails", sshfails),
        ("updates", updates),
        ("rebootrequired", rebootrequired),
        ("servers", servers_command),
        ("server", server_command),
        ("version", version),
    ]
    for command, callback in handlers:
        application.add_handler(CommandHandler(command, callback))
