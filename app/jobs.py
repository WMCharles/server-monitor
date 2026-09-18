from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from .alerts import AlertEngine
from .config import Settings
from .monitor import ServerMonitor
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


async def fast_monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    monitor: ServerMonitor = context.application.bot_data["monitor"]
    alerts: AlertEngine = context.application.bot_data["alerts"]

    try:
        snapshot = await run_blocking(monitor.fast_snapshot)
        events = alerts.evaluate_fast(snapshot)
        await _send_events(context, events)
    except Exception:
        logger.exception("Fast monitoring job failed")


async def slow_monitor_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    monitor: ServerMonitor = context.application.bot_data["monitor"]
    alerts: AlertEngine = context.application.bot_data["alerts"]

    try:
        snapshot = await run_blocking(monitor.slow_snapshot)
        events = alerts.evaluate_slow(snapshot)
        await _send_events(context, events)
    except Exception:
        logger.exception("Slow monitoring job failed")
