"""
ui_helpers.py

Small HTML/CSS helpers for dashboard.py's visual design - kept separate
so the page logic in dashboard.py isn't buried in inline HTML strings.

Typography: IBM Plex Mono, used uniformly for headers AND data - not
paired with a separate display sans. This is a deliberate reference to
real trading-terminal software (Bloomberg Terminal, Interactive
Brokers' TWS) rather than a "modern SaaS" template look: those tools
are monospace-first because everything on screen is a number that has
to align in a column, and going all-in on that instead of mixing in a
geometric display font is what actually reads as a purpose-built data
tool instead of a generic AI-generated app shell. Accent color is
amber (#FFB020) for the same reason - it's the classic amber-CRT
terminal color, not a "primary blue" default. Green/red are reserved
strictly for financial polarity (profit/loss, running/stopped), never
used as decoration, so they stay meaningful wherever they appear.

Colors come from .streamlit/config.toml's dark theme; this module only
adds typography and the bespoke components (the live status header,
badges, metric cards) config.toml can't express.
"""

from __future__ import annotations

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Mono', monospace;
}

[data-testid="stMetricValue"], .stDataFrame, code, pre {
    font-family: 'IBM Plex Mono', monospace !important;
}

h1, h2, h3, h4, h5, h6 {
    font-family: 'IBM Plex Mono', monospace !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em;
}

.stTabs [aria-selected="true"] {
    color: #FFB020 !important;
}
.stTabs [data-baseweb="tab-highlight"] {
    background-color: #FFB020 !important;
}

.si-header {
    margin-bottom: 1.75rem;
    padding-bottom: 1.25rem;
    border-bottom: 1px solid #262B36;
}
.si-header .si-title {
    font-size: 1.9rem;
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    line-height: 1.1;
    color: #F2F4F7;
}
.si-header .si-status {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.78rem;
    color: #8B92A3;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-top: 10px;
}
.si-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
}
.si-dot-live {
    background: #22C55E;
    box-shadow: 0 0 0 0 rgba(34,197,94,0.6);
    animation: si-pulse 2s infinite;
}
.si-dot-idle {
    background: #4B5262;
}
@keyframes si-pulse {
    0%   { box-shadow: 0 0 0 0 rgba(34,197,94,0.55); }
    70%  { box-shadow: 0 0 0 6px rgba(34,197,94,0); }
    100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); }
}

.si-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    border-radius: 2px;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.85rem;
    font-weight: 500;
    white-space: nowrap;
}
.si-badge-running { background: rgba(34,197,94,0.12); color: #22C55E; border: 1px solid rgba(34,197,94,0.35); }
.si-badge-stopped { background: rgba(239,68,68,0.10); color: #EF4444; border: 1px solid rgba(239,68,68,0.3); }
.si-badge::before { content: "\\25CF"; font-size: 0.6rem; }

.si-metric-card {
    background: #171A21;
    border: 1px solid #262B36;
    border-left: 2px solid #FFB020;
    border-radius: 2px;
    padding: 14px 16px;
    margin-bottom: 10px;
}
.si-metric-label {
    font-size: 0.72rem;
    color: #8B92A3;
    text-transform: uppercase;
    letter-spacing: 0.07em;
    margin-bottom: 6px;
}
.si-metric-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.5rem;
    font-weight: 600;
}
.si-metric-value.si-positive { color: #22C55E; }
.si-metric-value.si-negative { color: #EF4444; }
.si-metric-value.si-neutral { color: #E6E8EB; }
</style>
"""


def header_html(title: str, status_text: str, live: bool) -> str:
    """status_text: a real-time status line (e.g. scanner count, time
    since last trigger) - not decorative copy. live: whether to show
    the pulsing (vs idle) dot."""
    dot_class = "si-dot-live" if live else "si-dot-idle"
    return (
        f'<div class="si-header">'
        f'<div class="si-title">{title}</div>'
        f'<div class="si-status"><span class="si-dot {dot_class}"></span>{status_text}</div>'
        f"</div>"
    )


def status_badge_html(running: bool, pid: int | None = None) -> str:
    if running:
        return f'<span class="si-badge si-badge-running">RUNNING &middot; PID {pid}</span>'
    return '<span class="si-badge si-badge-stopped">STOPPED</span>'


def metric_card_html(label: str, value: str, sentiment: str = "neutral") -> str:
    """sentiment: 'positive', 'negative', or 'neutral' - controls value color."""
    return (
        f'<div class="si-metric-card">'
        f'<div class="si-metric-label">{label}</div>'
        f'<div class="si-metric-value si-{sentiment}">{value}</div>'
        f"</div>"
    )
