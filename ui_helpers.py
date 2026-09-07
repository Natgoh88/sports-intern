"""
ui_helpers.py

Small HTML/CSS helpers for dashboard.py's visual design - kept separate
so the page logic in dashboard.py isn't buried in inline HTML strings.

Typography: Space Grotesk for headings/labels, JetBrains Mono for
anything numeric (scores, odds, CLV%, pids) - deliberately not the
default Streamlit/Inter look, closer to a trading-terminal aesthetic
that fits a CLV-tracking tool. Colors come from .streamlit/config.toml's
dark theme; this module only adds typography and a few bespoke
components (status badges, metric cards) config.toml can't express.
"""

from __future__ import annotations

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Space Grotesk', -apple-system, sans-serif;
}

[data-testid="stMetricValue"], .stDataFrame, code, pre {
    font-family: 'JetBrains Mono', monospace !important;
}

h1, h2, h3, h4, h5, h6 {
    font-weight: 600 !important;
    letter-spacing: -0.02em;
}

.si-header {
    margin-bottom: 1.5rem;
}
.si-header .si-title {
    font-size: 2.1rem;
    font-weight: 700;
    letter-spacing: -0.03em;
    line-height: 1.1;
}
.si-header .si-tagline {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.78rem;
    color: #8B92A3;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-top: 4px;
}

.si-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    border-radius: 4px;
    font-family: 'JetBrains Mono', monospace;
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
    border-radius: 6px;
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
    font-family: 'JetBrains Mono', monospace;
    font-size: 1.5rem;
    font-weight: 600;
}
.si-metric-value.si-positive { color: #22C55E; }
.si-metric-value.si-negative { color: #EF4444; }
.si-metric-value.si-neutral { color: #E6E8EB; }
</style>
"""


def header_html(title: str, tagline: str) -> str:
    return f'<div class="si-header"><div class="si-title">{title}</div><div class="si-tagline">{tagline}</div></div>'


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
