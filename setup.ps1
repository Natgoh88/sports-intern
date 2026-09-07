# setup.ps1
# One-command setup: creates the venv, installs dependencies, and
# copies .env.example to .env if you don't have one yet. After this
# runs, start the app with:
#     .venv\Scripts\streamlit.exe run dashboard.py
# and fill in credentials from the Settings tab - no need to hand-edit
# .env.

$ErrorActionPreference = "Stop"

Write-Host "== Sports Intern setup ==" -ForegroundColor Cyan

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "Python not found on PATH. Install Python 3.11+ from https://python.org/downloads then re-run this script." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment (.venv)..."
    python -m venv .venv
} else {
    Write-Host ".venv already exists, skipping creation."
}

Write-Host "Installing dependencies..."
& ".venv\Scripts\pip.exe" install --upgrade pip --quiet
& ".venv\Scripts\pip.exe" install -r requirements.txt --quiet

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example - fill in your credentials from the dashboard's Settings tab." -ForegroundColor Yellow
} else {
    Write-Host ".env already exists, leaving it as-is."
}

Write-Host ""
Write-Host "Setup complete. Start the app with:" -ForegroundColor Green
Write-Host "    .venv\Scripts\streamlit.exe run dashboard.py"
Write-Host ""
Write-Host "Then open the Settings tab to add your Telegram bot token, chat ID, and API-Football key."
