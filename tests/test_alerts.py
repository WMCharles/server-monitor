from pathlib import Path
from tempfile import TemporaryDirectory

from app.alerts import AlertEngine
from app.config import Settings
from app.storage import Storage


def make_settings(db_path: Path) -> Settings:
    return Settings(
        telegram_bot_token="x",
        allowed_user_ids={1},
        alert_chat_id=1,
        server_name="test",
        timezone="UTC",
        check_interval_seconds=60,
        slow_check_interval_seconds=3600,
        command_timeout_seconds=8,
        http_timeout_seconds=5,
        alert_breach_count=2,
        alert_recovery_count=2,
        state_db=db_path,
        cpu_warning=80,
        cpu_critical=90,
        memory_warning=80,
        memory_critical=90,
        swap_warning=20,
        swap_critical=50,
        disk_warning=80,
        disk_critical=90,
        inode_warning=80,
        inode_critical=90,
        iowait_warning=15,
        iowait_critical=25,
        load_warning_multiplier=1.0,
        load_critical_multiplier=1.5,
    )


def test_alert_debounce_and_recovery():
    with TemporaryDirectory() as tmp:
        settings = make_settings(Path(tmp) / "state.sqlite3")
        engine = AlertEngine(settings, Storage(settings.state_db))

        assert engine.transition("cpu", "critical", "CPU", "95%") is None
        event = engine.transition("cpu", "critical", "CPU", "96%")
        assert event is not None
        assert event.level == "critical"

        assert engine.transition("cpu", "ok", "CPU", "50%") is None
        event = engine.transition("cpu", "ok", "CPU", "40%")
        assert event is not None
        assert event.level == "recovered"
