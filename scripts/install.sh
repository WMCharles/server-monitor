#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"

"$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

mkdir -p data logs

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created .env. Configure it before starting the bot."
fi

echo
echo "Installation complete."
echo "Next:"
echo "  1. Edit .env"
echo "  2. Run: .venv/bin/python main.py"
echo "  3. Send /whoami to the bot"
echo "  4. Put that user ID in ALLOWED_USER_IDS and restart"
