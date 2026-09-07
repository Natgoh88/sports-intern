#!/usr/bin/env bash
# setup.sh
# One-command setup: creates the venv, installs dependencies, and
# copies .env.example to .env if you don't have one yet. After this
# runs, start the app with:
#     source .venv/bin/activate && streamlit run dashboard.py
# and fill in credentials from the Settings tab - no need to hand-edit .env.

set -euo pipefail

echo "== Sports Intern setup =="

if ! command -v python3 &> /dev/null; then
    echo "python3 not found. Install Python 3.11+ (https://python.org/downloads) then re-run this script."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment (.venv)..."
    python3 -m venv .venv
else
    echo ".venv already exists, skipping creation."
fi

echo "Installing dependencies..."
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements.txt --quiet

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Created .env from .env.example - fill in your credentials from the dashboard's Settings tab."
else
    echo ".env already exists, leaving it as-is."
fi

echo ""
echo "Setup complete. Start the app with:"
echo "    source .venv/bin/activate && streamlit run dashboard.py"
echo ""
echo "Then open the Settings tab to add your Telegram bot token, chat ID, and API-Football key."
