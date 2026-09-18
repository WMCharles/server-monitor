# Telegram Server Monitor

A read-only Telegram operations bot that monitors a single Linux VM. It answers
manual status commands and raises automatic threshold alerts using the same
monitoring code.

The bot **never** executes shell commands, restarts services/containers, reboots
the host, or runs arbitrary SQL. It only reads system state and reports it.

## Features

- CPU utilisation, load average and I/O wait
- RAM and swap usage
- Filesystem capacity and inode usage
- Uptime and reboot detection
- systemd service status and failed units
- Process counts, top CPU and top memory processes
- Docker daemon and container state
- Configured HTTP/API health endpoints
- TCP port reachability
- Network counters and interface information
- PostgreSQL/MySQL/TCP database reachability
- TLS certificate expiry
- Backup freshness
- Recent journal errors
- Application log files (allowlisted) with new-error alerting
- Recent failed SSH authentication attempts
- Pending OS package updates
- Reboot-required detection
- Persistent active-alert state using SQLite
- Multi-VM mode: one central bot + read-only SSH agents per host
- Consecutive-check alert debounce and recovery notifications
- Telegram user allowlist
- Telegram command-menu registration

## Requirements

Recommended:

- Ubuntu/Debian Linux VM
- Python 3.11+
- systemd
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

Optional features depend on tools installed on the monitored VM:

- `docker` for Docker checks
- `pg_isready` for enhanced PostgreSQL checks
- `journalctl` for logs/security checks
- `apt` or `dnf` for update checks

Runtime dependencies (see `requirements.txt`):

- `python-telegram-bot[job-queue]==22.8`
- `psutil`
- `httpx`
- `python-dotenv`

## Project layout

```text
server-monitor/
├── app/
│   ├── __init__.py
│   ├── alerts.py       # debounce + alert lifecycle
│   ├── commands.py     # Telegram command handlers
│   ├── config.py       # .env parsing and validation
│   ├── fleet.py        # multi-VM transport (local + SSH agents)
│   ├── jobs.py         # fast/slow/log monitoring jobs
│   ├── logwatch.py     # app-log tailing + new-error alerts
│   ├── monitor.py      # read-only collectors (the core)
│   ├── storage.py      # SQLite alert/meta persistence
│   └── utils.py
├── agent.py            # remote snapshot agent (multi-VM)
├── data/               # SQLite state (gitignored)
├── logs/               # log files (gitignored)
├── logrotate/
│   └── ridemeds-laravel.conf
├── scripts/
│   └── install.sh
├── systemd/
│   └── server-monitor.service
├── tests/
│   └── test_alerts.py
├── .env.example
├── .gitignore
├── DEPLOYMENT.md       # production deployment runbook
├── hosts.example.json  # multi-VM inventory example
├── LICENSE
├── main.py
├── README.md
└── requirements.txt
```

## Install

```bash
sudo mkdir -p /opt/server-monitor
sudo chown "$USER":"$USER" /opt/server-monitor
cd /opt/server-monitor

chmod +x scripts/install.sh
./scripts/install.sh
```

Or manually:

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
mkdir -p data logs
```

> On Debian/Ubuntu you may need `sudo apt install python3-venv` first.

## Configure

Edit `.env` (never commit it). Minimum viable config:

```env
TELEGRAM_BOT_TOKEN=123456:your-token
SERVER_NAME=production-01
ALLOWED_USER_IDS=123456789
ALERT_CHAT_ID=123456789
```

To discover your numeric Telegram user ID, start the bot with
`ALLOWED_USER_IDS` empty and send `/whoami`.

Full reference (see `.env.example` for all keys):

| Variable | Purpose | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather | *(required)* |
| `ALLOWED_USER_IDS` | Comma-separated numeric IDs allowed to command the bot | *(empty)* |
| `ALERT_CHAT_ID` | Chat/group that receives automatic alerts | *(empty)* |
| `SERVER_NAME` | Display name in messages | `production-01` |
| `TIMEZONE` | Display timezone | `Africa/Nairobi` |
| `CHECK_INTERVAL_SECONDS` | Fast check interval (min 15) | `60` |
| `SLOW_CHECK_INTERVAL_SECONDS` | Slow check interval (min 300) | `3600` |
| `ALERT_BREACH_COUNT` | Consecutive breaches before alerting | `3` |
| `ALERT_RECOVERY_COUNT` | Consecutive healthy checks before recovery | `2` |
| `STATE_DB` | SQLite state path | `./data/server_monitor.sqlite3` |
| `DISK_MOUNTS` | Filesystems to inspect | `/` |
| `MONITORED_SERVICES` | systemd services to watch | *(empty)* |
| `LOG_SERVICES` | Services allowed via `/logs` | *(empty)* |
| `MONITORED_CONTAINERS` | Containers that must be running | *(empty)* |
| `LOG_FILES` | `name\|/absolute/path` app logs readable via `/logs` and scanned for errors | *(empty)* |
| `LOG_CONTAINERS` | Containers readable via `/logs` (`docker logs`) | *(empty)* |
| `LOG_ERROR_PATTERNS` | Substrings marking a log line as an error | `.ERROR,.CRITICAL,.ALERT,.EMERGENCY` |
| `LOG_ERROR_THRESHOLD` | New matching lines per check before alerting | `5` |
| `LOG_CHECK_INTERVAL_SECONDS` | App-log scan interval (min 60) | `300` |
| `HEALTH_URLS` | `name\|URL` pairs, comma-separated | *(empty)* |
| `MONITORED_PORTS` | `name\|host\|port` triples | *(empty)* |
| `DB_TYPE` | `none`, `postgres`, `mysql`, `tcp` | `none` |
| `SSL_TARGETS` | `name\|hostname\|port` triples | *(empty)* |
| `BACKUP_TARGETS` | `name\|path\|max_age_hours` triples | *(empty)* |

Thresholds (`CPU_WARNING`/`CPU_CRITICAL`, `MEMORY_*`, `SWAP_*`, `DISK_*`,
`INODE_*`, `IOWAIT_*`, `LOAD_*_MULTIPLIER`) all have sane defaults; see
`.env.example`.

Malformed list entries (wrong number of `|`-separated fields) make the bot
refuse to start. Always validate after editing by restarting the service.

## Commands

```text
/start  /help  /status  /system
/cpu  /load  /memory  /swap  /disk  /io  /inode  /uptime
/services  /failed
/processes  /topcpu  /topmem
/docker  /containers
/health  /ports  /network  /db
/logs <service|file|container> [lines]  /errors
/ssl  /backups
/alerts  /thresholds
/security  /sshfails  /updates  /rebootrequired
/whoami  /version
```

`/status` is the primary operational summary.

## Alert lifecycle

The bot does not repeat the same alert every check. With the defaults above:

```text
CPU crosses 95%
check 1 -> pending
check 2 -> pending
check 3 -> SERVER ALERT — CRITICAL

CPU remains 97%
-> no duplicate alert

CPU returns to normal
healthy check 1 -> pending recovery
healthy check 2 -> RECOVERED
```

Active alerts are persisted in SQLite and listed by `/alerts`. The in-memory
breach counter resets if the service restarts, so a fresh breach needs a full
`ALERT_BREACH_COUNT` checks again.

## Application logs

`/logs <target> [lines]` resolves the target in this order: a systemd unit from
`LOG_SERVICES` (journald), then an app **file** from `LOG_FILES`, then a
**container** from `LOG_CONTAINERS` (`docker logs`). Only allowlisted names are
accepted — arbitrary paths are always rejected.

When `LOG_FILES` is set, a separate job (`LOG_CHECK_INTERVAL_SECONDS`, default
300s) tails each file from a persisted byte-offset cursor and raises a `critical`
alert when the number of new lines matching `LOG_ERROR_PATTERNS` reaches
`LOG_ERROR_THRESHOLD`. The first scan of a file only records its current size,
so historical errors never alert. Rotation/truncation is detected and the
cursor resets safely.

## Run with systemd

Edit `systemd/server-monitor.service` and set `User=`/`Group=` to the account
that owns the install directory, then:

```bash
sudo cp systemd/server-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now server-monitor
sudo systemctl status server-monitor
journalctl -u server-monitor -f
```

## Testing

```bash
python3 -m compileall app main.py
.venv/bin/pip install pytest
.venv/bin/pytest -q
```

## Security notes

1. Keep `.env` mode `600`.
2. Never commit the Telegram bot token.
3. Set `ALLOWED_USER_IDS`; an empty allowlist disables commands.
4. Do not add shell/reboot/restart capabilities unless you accept the risk.
5. `/logs` only accepts services listed in `LOG_SERVICES` (no user paths).
6. The bot does not execute arbitrary SQL.
7. Run it as a non-root user.
8. Rotate the bot token immediately if it is exposed (BotFather → `/revoke`).

See [DEPLOYMENT.md](DEPLOYMENT.md) for the production setup, permissions,
backup cron, and operations runbook.

## Multi-VM mode

The same bot can monitor several hosts. One **central** bot polls a read-only
**agent** on each VM over SSH; there is still a single Telegram token and a
single alert chat.

```
VM A agent ─┐
VM B agent ─┼─► central bot (Telegram) ─► one alert chat
VM C agent ─┘     ssh each host, merge snapshots
```

- `agent.py` runs on each VM and prints a JSON snapshot using that host's own
  `.env`. It never holds a Telegram token.
- The central bot reads a JSON inventory (`HOSTS_FILE`) and answers commands for
  the selected host. Alerts are keyed `host:metric` and the alert text names the
  host.
- A `target` of `local` runs the collector in-process (use it for the central
  host itself).

`hosts.example.json`:

```json
[
  {"name": "mpanzi", "target": "local"},
  {"name": "kifaru", "target": "kifaru", "command": "cd /opt/server-monitor && .venv/bin/python agent.py"},
  {"name": "delware", "target": "delware", "command": "cd /opt/server-monitor && .venv/bin/python agent.py"}
]
```

Enable it with:

```env
HOSTS_FILE=/opt/server-monitor/hosts.json
```

Additional commands in multi-VM mode:

```text
/servers            list configured servers (▶ marks the active one)
/server <name>      switch the active server for this chat
/status all         compact health for every server
```

Every other command (`/cpu`, `/disk`, `/services`, `/logs`, …) applies to the
active server. With `HOSTS_FILE` unset the bot runs in single-VM mode exactly as
before. See [DEPLOYMENT.md](DEPLOYMENT.md) for the full rollout, including SSH
trust and per-host collectors.

## License

Released under the [MIT License](LICENSE).
