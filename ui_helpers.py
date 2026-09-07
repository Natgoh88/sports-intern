"""
ui_helpers.py

Small HTML/CSS helpers for dashboard.py's visual design - kept separate
so the page logic in dashboard.py isn't buried in inline HTML strings.

Typography system (see README's design notes / the typography scale
this was built from):

- IBM Plex Sans for prose - section headers, captions, table text
  columns, form labels. Proportional fonts read faster than monospace
  for actual reading, which is most of what's on this dashboard.
- IBM Plex Mono reserved for anything that is a *number that changes*:
  the brand title/status line (scanner counts), metric card values
  (CLV%, net units), status badge PIDs, and dataframe numeric columns.
  A monospace font's fixed glyph width solves the "digits jitter
  horizontally when they update" problem more robustly than
  `font-variant-numeric: tabular-nums` alone, since tabular-nums only
  fixes digit width *within a proportional font* - a true monospace
  font is tabular for every character by construction. The
  `.tabular-nums` utility class below still exists for the one spot
  that needs it despite living in the Sans context (see dashboard.py).
- Same "Plex" family for both, not two unrelated fonts glued together -
  and deliberately not Inter, which is the single most common default
  in AI-generated app templates and the opposite of what a purpose-
  built data tool should look like.

Colors come from .streamlit/config.toml's dark theme; this module only
adds typography and the bespoke components (the live status header,
badges, metric cards) config.toml can't express. Amber (#FFB020) is
the brand accent (classic amber-CRT terminal color); green/red are
reserved strictly for financial polarity (profit/loss, running/
stopped) and never used as decoration, so they stay meaningful
wherever they appear.
"""

from __future__ import annotations

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', -apple-system, sans-serif;
}

h1, h2, h3, h4, h5, h6 {
    font-family: 'IBM Plex Sans', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: -0.01em;
}

/* anything that is a live-updating number: metric values, dataframe
cells, badges, inline code. Monospace glyph widths make digit changes
non-jittering without needing font-variant-numeric everywhere. */
[data-testid="stMetricValue"], .stDataFrame, code, pre {
    font-family: 'IBM Plex Mono', monospace !important;
}

/* explicit utility for the one place a live number sits inside a Sans
(proportional) context instead of a Mono one - font-variant-numeric
only matters here, since Mono elements are already tabular by
construction. */
.tabular-nums {
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum" 1;
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
    font-family: 'IBM Plex Mono', monospace;
    font-size: 1.875rem;
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
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.78rem;
    font-weight: 500;
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
    font-family: 'IBM Plex Sans', sans-serif;
    font-size: 0.72rem;
    font-weight: 500;
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
