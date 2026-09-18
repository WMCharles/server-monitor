from __future__ import annotations

import asyncio
import logging

from telegram.ext import ContextTypes

from .alerts import AlertEngine
from .config import Settings
from .logwatch import AppLogWatcher
from .monitor import ServerMonitor
from .storage import Storage
from .utils import run_blocking

logger = logging.getLogger(__name__)


async def _send_events(context: ContextTypes.DEFAULT_TYPE, events) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if settings.alert_chat_id is None:
        return

    for event in events:
        try:
            await context.bot.send_message(
                chat_id=settings.alert_chat_id,
                text=event.text,
            )
        except Exception:
            logger.exception("Failed to send alert %s", event.key)


async def _evaluate_fleet(context: ContextTypes.DEFAULT_TYPE, kind: str):
    """Evaluate fast/slow checks for every configured server.

    Falls back to the single local monitor when no fleet is configured.
    """
    alerts: AlertEngine = context.application.bot_data["alerts"]
    fleet = context.application.bot_data.get("fleet")
    evaluate = alerts.evaluate_slow if kind == "slow" else alerts.evaluate_fast

    if fleet is None:
        monitor: ServerMonitor = context.application.bot_data["monitor"]
        snapshot = await run_blocking(
            monitor.slow_snapshot if kind == "slow" else monitor.fast_snapshot
        )
        return evaluate(snapshot)

    async def one(name: str):
        member = fleet.get(name)
        try:
            snapshot = await run_blocking(
                member.slow_snapshot if kind == "slow" else member.fast_snapshot
            )
        except Exception as exc:
            logger.warning("%s %s snapshot failed: %s", name, kind, exc)
            return []
        return evaluate(snapshot, name)

    results = await asyncio.gather(*(one(name) for name in fleet.names()))
    return [event for group in results for event in group]


async def fast_monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        events = await _evaluate_fleet(context, "fast")
        await _send_events(context, events)
    except Exception:
        logger.exception("Fast monitoring job failed")


async def slow_monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        events = await _evaluate_fleet(context, "slow")
        await _send_events(context, events)
    except Exception:
        logger.exception("Slow monitoring job failed")


async def log_monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    monitor: ServerMonitor = context.application.bot_data["monitor"]
    storage: Storage = context.application.bot_data["storage"]
    alerts: AlertEngine = context.application.bot_data["alerts"]

    try:
        watcher = AppLogWatcher(settings, monitor, storage, alerts)
        events = await run_blocking(watcher.scan)
        await _send_events(context, events)
    except Exception:
        logger.exception("Log monitoring job failed")
