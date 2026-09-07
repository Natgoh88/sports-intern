"""
scanner_manager.py

Starts/stops run_basketball.py and run_soccer.py as background
subprocesses, and tracks whether each is still alive - so the dashboard
can offer Start/Stop buttons instead of requiring a terminal.

State has to survive Streamlit's rerun-the-whole-script-on-every-click
model, so the PID for each scanner is written to a small file under
.run/ instead of a Python variable. Liveness is checked with psutil
(cross-platform; works the same on the Windows dev box this was built
on and on Linux if this ever moves to a VPS/Docker).
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import psutil

BASE_DIR = Path(__file__).parent
RUN_DIR = BASE_DIR / ".run"
RUN_DIR.mkdir(exist_ok=True)

SCANNERS = {
    "basketball": BASE_DIR / "run_basketball.py",
    "soccer": BASE_DIR / "run_soccer.py",
    # named scanner_watchdog.py, not watchdog.py - see that file's
    # docstring for why (it collides with the real `watchdog` PyPI
    # package Streamlit depends on)
    "watchdog": BASE_DIR / "scanner_watchdog.py",
    "api": BASE_DIR / "api.py",
}


def _pid_file(name: str) -> Path:
    return RUN_DIR / f"{name}.pid"


def _heartbeat_file(name: str) -> Path:
    return RUN_DIR / f"{name}.heartbeat"


def write_heartbeat(name: str) -> None:
    """Called by a scanner on every loop iteration so the watchdog can
    tell 'alive but hung' apart from 'genuinely making progress' - a
    bare process-liveness check can't see the difference."""
    _heartbeat_file(name).write_text(str(time.time()))


def heartbeat_age_seconds(name: str) -> float | None:
    hb_file = _heartbeat_file(name)
    if not hb_file.exists():
        return None
    try:
        return time.time() - float(hb_file.read_text().strip())
    except (ValueError, OSError):
        return None


def status(name: str) -> dict:
    pid_file = _pid_file(name)
    if not pid_file.exists():
        return {"running": False, "pid": None}

    pid = int(pid_file.read_text().strip())
    if psutil.pid_exists(pid):
        try:
            proc = psutil.Process(pid)
            # guard against a stale pid file pointing at an unrelated
            # process that happened to reuse this pid after a reboot
            if "python" in proc.name().lower():
                return {"running": True, "pid": pid}
        except psutil.NoSuchProcess:
            pass

    pid_file.unlink(missing_ok=True)
    return {"running": False, "pid": None}


def start(name: str) -> dict:
    if status(name)["running"]:
        return status(name)

    script = SCANNERS[name]
    log_path = RUN_DIR / f"{name}.log"
    log_file = open(log_path, "a")

    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        [sys.executable, "-u", str(script)],
        cwd=str(BASE_DIR),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    _pid_file(name).write_text(str(proc.pid))

    # the watchdog is what notices either scanner going dark, so it
    # needs to already be running whenever one is - starting it here
    # means a beginner gets that protection without knowing to ask for it
    if name in ("soccer", "basketball") and not status("watchdog")["running"]:
        start("watchdog")

    return {"running": True, "pid": proc.pid}


def stop(name: str) -> dict:
    st = status(name)
    if st["running"]:
        try:
            psutil.Process(st["pid"]).terminate()
        except psutil.NoSuchProcess:
            pass
    _pid_file(name).unlink(missing_ok=True)
    return {"running": False, "pid": None}


def recent_log(name: str, lines: int = 30) -> str:
    log_path = RUN_DIR / f"{name}.log"
    if not log_path.exists():
        return ""
    with open(log_path) as f:
        return "".join(f.readlines()[-lines:])
