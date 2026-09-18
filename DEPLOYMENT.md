# Deployment Runbook

How this bot is deployed in production, and how to operate it. Everything here
is host-local and non-destructive; the bot itself is read-only.

> Replace `<host>`, `<domain>`, `<bot-token>`, `<chat-id>` and `<user-id>` with
> your real values. Never commit a filled-in `.env`.

## Reference environment

| Item | Value |
|---|---|
| OS | Ubuntu 24.04 LTS |
| Python | 3.12 |
| Init | systemd 255 |
| Install path | `/opt/server-monitor` |
| Service | `server-monitor.service` |
| Run-as user | `zogratis` (non-root) |
| State DB | `/opt/server-monitor/data/server_monitor.sqlite3` |
| Log file | `/opt/server-monitor/logs/server-monitor.log` |

## Directory layout

```text
/opt/server-monitor/
├── .env                 # mode 600, not in git
├── .venv/               # virtualenv
├── app/                 # application code
├── data/                # SQLite state
├── logs/                # rotating-ish log file
├── main.py
└── ...
```

## 1. Install

```bash
sudo mkdir -p /opt/server-monitor
sudo chown "$USER":"$USER" /opt/server-monitor
# copy project files into /opt/server-monitor
cd /opt/server-monitor

sudo apt-get update
sudo apt-get install -y python3.12-venv   # if ensurepip is unavailable

chmod +x scripts/install.sh
./scripts/install.sh
```

## 2. Configure `.env`

```bash
nano /opt/server-monitor/.env
chmod 600 /opt/server-monitor/.env
```

A representative production config:

```env
# Telegram
TELEGRAM_BOT_TOKEN=<bot-token>
ALLOWED_USER_IDS=<user-id>
ALERT_CHAT_ID=<chat-id>

# General
SERVER_NAME=<host>
TIMEZONE=Africa/Nairobi
CHECK_INTERVAL_SECONDS=60
SLOW_CHECK_INTERVAL_SECONDS=3600
ALERT_BREACH_COUNT=3
ALERT_RECOVERY_COUNT=2
STATE_DB=./data/server_monitor.sqlite3

# Thresholds (defaults)
CPU_WARNING=85
CPU_CRITICAL=95
MEMORY_WARNING=85
MEMORY_CRITICAL=95
SWAP_WARNING=20
SWAP_CRITICAL=50
DISK_WARNING=80
DISK_CRITICAL=90
INODE_WARNING=80
INODE_CRITICAL=90
IOWAIT_WARNING=15
IOWAIT_CRITICAL=25
LOAD_WARNING_MULTIPLIER=1.0
LOAD_CRITICAL_MULTIPLIER=1.5

# Monitored resources
DISK_MOUNTS=/
MONITORED_SERVICES=nginx,php8.4-fpm,docker,ssh
LOG_SERVICES=nginx,php8.4-fpm,docker,ssh
LOG_CONTAINERS=<container-a>
LOG_FILES=api|/var/www/app/storage/logs/laravel.log
LOG_ERROR_THRESHOLD=5
LOG_CHECK_INTERVAL_SECONDS=300
MONITORED_CONTAINERS=<container-a>,<container-b>
HEALTH_URLS=<name>|http://127.0.0.1:<port>/
MONITORED_PORTS=ssh|127.0.0.1|22,http|127.0.0.1|80,https|127.0.0.1|443

# Database
DB_TYPE=postgres
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=

# TLS certificates
SSL_TARGETS=<name>|<domain>|443

# Backups
BACKUP_TARGETS=<name>|/home/zogratis/dbackups|96

# Journal / security
ERROR_LOOKBACK_MINUTES=30
SSH_LOOKBACK_MINUTES=60
```

Validate before starting (catches malformed `|` lists):

```bash
cd /opt/server-monitor
PYTHONPATH=/opt/server-monitor .venv/bin/python -c \
  'from app.config import Settings; s=Settings.from_env(); s.validate(); print("config OK")'
```

## 3. Install the systemd unit

Edit `User=`/`Group=` first:

```bash
sudo sed -i 's/^User=ubuntu/User=zogratis/; s/^Group=ubuntu/Group=zogratis/' \
  /opt/server-monitor/systemd/server-monitor.service
sudo cp /opt/server-monitor/systemd/server-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now server-monitor
```

The unit includes hardening (`NoNewPrivileges`, `PrivateTmp`,
`ProtectSystem=full`, `ReadWritePaths=/opt/server-monitor/data /opt/server-monitor/logs`).

## 4. Grant read permissions

The bot runs as a non-root user and needs enough access to read what it
monitors:

```bash
# Docker socket access (only if monitoring containers)
sudo usermod -aG docker zogratis

# Full journal read access for /logs, /errors, /security, /sshfails
sudo usermod -aG systemd-journal zogratis

sudo systemctl restart server-monitor
```

Membership in the `docker` group is effectively root-equivalent — grant only if
needed.

## 5. Application log monitoring

Tail allowlisted application logs and alert on new errors:

```env
LOG_FILES=api|/var/www/app/storage/logs/laravel.log,worker|/var/www/app/storage/logs/worker.log
LOG_CONTAINERS=<container-a>
LOG_ERROR_PATTERNS=.ERROR,.CRITICAL,.ALERT,.EMERGENCY
LOG_ERROR_THRESHOLD=5
LOG_CHECK_INTERVAL_SECONDS=300
```

- `/logs api 100` tails a file, `/logs <container>` uses `docker logs`,
  `/logs nginx` uses journald.
- Only names in the allowlists are accepted; arbitrary paths are rejected.
- The scanner keeps a byte-offset cursor per file in the state DB and alerts
  only on **new** matching lines (the first scan seeds the cursor).
- The service user needs read access to each file. `ProtectSystem=full` still
  permits reads under `/var`.

**Rotate large app logs.** Laravel writes a single `laravel.log` that can grow
without bound. Install the bundled rule:

```bash
sudo cp logrotate/ridemeds-laravel.conf /etc/logrotate.d/ridemeds-laravel
sudo logrotate -d /etc/logrotate.d/ridemeds-laravel   # dry run
```

## 6. Database backup cron

The monitor only *reports* backup freshness; it does not create backups. A
companion script keeps a daily dump of the PostgreSQL container and prunes old
ones.

`/home/zogratis/scripts/backup-ridemeds-db.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

CONTAINER=ridemeds-postgres
DB=ridemeds
DIR=/home/zogratis/dbackups
KEEP_DAYS=30
STAMP=$(date +%F)
FINAL="$DIR/ridemeds-$STAMP.dump"

mkdir -p "$DIR/.tmp"
TMP=$(mktemp "$DIR/.tmp/ridemeds-XXXXXX.dump")
trap 'rm -f "$TMP"' EXIT

docker inspect -f '{{.State.Running}}' "$CONTAINER" | grep -q true
docker exec -u postgres "$CONTAINER" pg_dump -U postgres -d "$DB" -Fc > "$TMP"

test -s "$TMP"
docker exec -i "$CONTAINER" pg_restore -l < "$TMP" > /dev/null

mv -f "$TMP" "$FINAL"
( cd "$DIR" && sha256sum "$(basename "$FINAL")" > "$(basename "$FINAL").sha256" )

find "$DIR" -maxdepth 1 -name 'ridemeds-*.dump' -mtime +"$KEEP_DAYS" -delete
find "$DIR" -maxdepth 1 -name 'ridemeds-*.dump.sha256' -mtime +"$KEEP_DAYS" -delete

echo "$(date '+%F %T') OK $(basename "$FINAL") $(stat -c%s "$FINAL") bytes"
```

Install:

```bash
chmod 750 /home/zogratis/scripts/backup-ridemeds-db.sh
( crontab -l 2>/dev/null; \
  echo "# ridemeds database backup (daily)"; \
  echo "15 2 * * * /home/zogratis/scripts/backup-ridemeds-db.sh >> /home/zogratis/dbackups/backup.log 2>&1" \
) | crontab -
```

Notes:

- Writes to `dbackups/.tmp/` and renames into place, so a failed dump never
  overwrites a good one. The monitor only stats files, so the `.tmp/` subdir is
  ignored.
- No DB password is stored: `docker exec -u postgres` uses peer auth inside the
  container.
- Set `BACKUP_TARGETS=<name>|/home/zogratis/dbackups|<max_age_hours>` to match
  the cadence (e.g. `96` for a tolerant 4-day window, `26` for strict daily).

## 7. Timezone

The bot displays times using `TIMEZONE`, independent of the host clock. To set
the host itself to East Africa Time:

```bash
sudo timedatectl set-timezone Africa/Nairobi
echo "Africa/Nairobi" | sudo tee /etc/timezone
sudo systemctl reload nginx php8.4-fpm   # so web logs follow
sudo systemctl restart server-monitor
```

Applications pinned to UTC (Laravel `config/app.php`, PHP `date.timezone`, the
PostgreSQL container) are unaffected. Cron entries then run on EAT wall-clock.

## Operations

```bash
sudo systemctl status server-monitor
sudo journalctl -u server-monitor -f
sudo systemctl restart server-monitor     # after any .env change
```

Inspect state directly:

```bash
cd /opt/server-monitor
PYTHONPATH=/opt/server-monitor .venv/bin/python - <<'PY'
import sqlite3
c = sqlite3.connect("data/server_monitor.sqlite3")
for r in c.execute("SELECT alert_key, level, active, first_seen FROM alerts"):
    print(r)
PY
```

### Rotate the bot token

1. BotFather → `/revoke` → copy the new token.
2. `sudo sed -i 's|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=<new>|' /opt/server-monitor/.env`
3. `sudo systemctl restart server-monitor`

## Troubleshooting

| Symptom | Check |
|---|---|
| Bot silent, no reply | `systemctl status server-monitor`; token validity (`getMe`) |
| `Unauthorized.` reply | Your ID is not in `ALLOWED_USER_IDS` |
| Service won't start | Malformed `|` list in `.env`; run the config validation command |
| `/alerts` shows nothing | Normal when healthy — only *active* sustained breaches appear |
| No backups alert | `BACKUP_TARGETS` empty, or newest file is within `max_age_hours` |
| `/logs` empty | User missing `systemd-journal` group membership |
| Docker checks fail | User missing `docker` group membership |
| Slow checks absent | They run every `SLOW_CHECK_INTERVAL_SECONDS` (default hourly) |

## Security checklist

- [ ] `.env` is mode `600` and gitignored
- [ ] `ALLOWED_USER_IDS` is set (not empty)
- [ ] Bot token rotated after any exposure
- [ ] Bot runs as a non-root user
- [ ] No reboot/shell/restart command has been added
- [ ] `docker`/`systemd-journal` membership is the minimum required
