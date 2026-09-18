from __future__ import annotations

import logging
from pathlib import Path

from telegram import BotCommand
from telegram.ext import Application

from app.alerts import AlertEngine
from app.commands import register_handlers
from app.config import Settings
from app.jobs import fast_monitor_job, log_monitor_job, slow_monitor_job
from app.monitor import ServerMonitor
from app.storage import Storage


def configure_logging() -> None:
    Path("logs").mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("logs/server-monitor.log"),
        ],
    )


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("status", "Overall server health"),
        BotCommand("system", "Host, OS and kernel information"),
        BotCommand("cpu", "CPU information"),
        BotCommand("load", "Load average and per-core ratio"),
        BotCommand("memory", "RAM usage"),
        BotCommand("swap", "Swap usage"),
        BotCommand("disk", "Filesystem usage"),
        BotCommand("io", "Disk I/O rates"),
        BotCommand("inode", "Filesystem inode usage"),
        BotCommand("uptime", "Uptime and boot information"),
        BotCommand("services", "Monitored systemd services"),
        BotCommand("failed", "Failed systemd units"),
        BotCommand("processes", "Process summary"),
        BotCommand("topcpu", "Top CPU processes"),
        BotCommand("topmem", "Top memory processes"),
        BotCommand("docker", "Docker daemon status"),
        BotCommand("containers", "Container status"),
        BotCommand("health", "Configured HTTP health checks"),
        BotCommand("ports", "Configured TCP port checks"),
        BotCommand("network", "Network counters/interfaces"),
        BotCommand("db", "Database reachability"),
        BotCommand("logs", "Recent logs for an allowed service"),
        BotCommand("errors", "Recent system error logs"),
        BotCommand("ssl", "TLS certificate expiry"),
        BotCommand("backups", "Backup freshness"),
        BotCommand("alerts", "Active alerts"),
        BotCommand("thresholds", "Configured thresholds"),
        BotCommand("security", "Security summary"),
        BotCommand("sshfails", "Recent failed SSH attempts"),
        BotCommand("updates", "Pending package updates"),
        BotCommand("rebootrequired", "Check whether reboot is required"),
        BotCommand("whoami", "Show your Telegram user/chat ID"),
        BotCommand("version", "Bot version"),
        BotCommand("help", "Show command help"),
    ]
    await application.bot.set_my_commands(commands)

    monitor: ServerMonitor = application.bot_data["monitor"]
    storage: Storage = application.bot_data["storage"]
    settings: Settings = application.bot_data["settings"]

    current_boot_id = monitor.boot_id()
    previous_boot_id = storage.get_meta("boot_id")
    storage.set_meta("boot_id", current_boot_id)

    if (
        previous_boot_id
        and previous_boot_id != current_boot_id
        and settings.alert_chat_id is not None
    ):
        await application.bot.send_message(
            chat_id=settings.alert_chat_id,
            text=(
                f"SERVER REBOOT DETECTED\n\n"
                f"Server: {settings.server_name}\n"
                f"Boot time: {monitor.boot_time_text()}"
            ),
        )


def main() -> None:
    configure_logging()

    settings = Settings.from_env()
    settings.validate()

    storage = Storage(settings.state_db)
    monitor = ServerMonitor(settings)
    alerts = AlertEngine(settings, storage)

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .build()
    )

    application.bot_data["settings"] = settings
    application.bot_data["storage"] = storage
    application.bot_data["monitor"] = monitor
    application.bot_data["alerts"] = alerts

    register_handlers(application)

    if application.job_queue is None:
        raise RuntimeError(
            'JobQueue unavailable. Install with: pip install "python-telegram-bot[job-queue]==22.8"'
        )

    application.job_queue.run_repeating(
        fast_monitor_job,
        interval=settings.check_interval_seconds,
        first=10,
        name="fast-monitor",
    )
    application.job_queue.run_repeating(
        slow_monitor_job,
        interval=settings.slow_check_interval_seconds,
        first=30,
        name="slow-monitor",
    )

    if settings.log_files:
        application.job_queue.run_repeating(
            log_monitor_job,
            interval=settings.log_check_interval_seconds,
            first=60,
            name="log-monitor",
        )

    logging.getLogger(__name__).info(
        "Starting server monitor for %s", settings.server_name
    )
    application.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
