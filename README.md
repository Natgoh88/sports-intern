# Sports Intern

[![tests](https://github.com/Natgoh88/sports-intern/actions/workflows/tests.yml/badge.svg)](https://github.com/Natgoh88/sports-intern/actions/workflows/tests.yml)

A real-time event-detection system for live basketball and soccer, built
around an async polling pipeline, a pluggable adapter layer for
third-party sports data APIs, and a probability engine that de-vigs
market odds to compute fair closing-line value. It watches live games
for a handful of specific, rules-based situations where the market
tends to lag reality for a few minutes, and pushes an alert to Telegram
the moment one fires. It does not try to predict winners - it solves
the attention problem, not the prediction problem.

Everything - credentials, trigger thresholds, and the scanners
themselves - is controlled from a single Streamlit dashboard. No code
edits or hand-written `.env` files required to run it.

## Screenshots

*(Add real screenshots here before publishing - the dashboard's four
tabs are exactly what a reader will want to see first.)*

```
docs/screenshot-live.png       - Live tab: recent triggers + CLV panel
docs/screenshot-analytics.png  - Analytics tab: per-rule CLV + trigger frequency
docs/screenshot-scanners.png   - Scanners tab: start/stop controls + watchdog
docs/screenshot-settings.png   - Settings tab: credentials + threshold form
```

Run the dashboard (`streamlit run dashboard.py`), take a screenshot of
each tab, save them under `docs/` with the names above, and this
section will render inline on GitHub.

## Quick start

```bash
git clone <this repo>
cd sports-intern

# Windows
.\setup.ps1

# macOS / Linux
./setup.sh

# then start the app - everything else (credentials, thresholds, the
# scanners themselves) is configured from the browser, not the terminal
.venv\Scripts\streamlit.exe run dashboard.py   # Windows
.venv/bin/streamlit run dashboard.py           # macOS / Linux
```

Open `http://localhost:8501`, go to **Settings**, and fill in:
- A Telegram bot token + chat ID (message [@BotFather](https://t.me/BotFather) on Telegram to create a bot, `/newbot`)
- An API-Football key (free, 100 req/day - sign up at [dashboard.api-football.com](https://dashboard.api-football.com))

Use the **Send Telegram test message** / **Check API-Football key**
buttons to confirm both work, then go to the **Scanners** tab and hit
**Start**.

### Docker (untested locally - no Docker in the environment this was built in)

```bash
cp .env.example .env   # fill in credentials, or leave blank and use the Settings tab after
docker compose up
```

## Architecture

```
                         +---------------------------+
                         |   Data Sources (poll)      |
                         |  - ESPN hidden endpoints    |
                         |  - API-Football (+ /odds)   |
                         +--------------+--------------+
                                        |  raw JSON, per game
                                        v
                         +---------------------------+
                         |   Entity Resolution         |
                         |   entity_resolution.py       |
                         +--------------+--------------+
                                        |  clean GameState objects
                                        v
                +-----------------------+-----------------------+
                v                                                v
   +---------------------------+                   +---------------------------+
   |  Basketball Trigger Engine |                   |  Soccer polling loop       |
   |  basketball_scanner.py     |                   |  run_soccer.py             |
   |  run_basketball.py         |                   |  (window-aware adaptive    |
   |  (continuous, ~20s ticks)  |                   |   polling - see below)     |
   +--------------+--------------+                   +--------------+--------------+
                  |  TriggerEvent                                   |  TriggerEvent
                  +-----------------------+-------------------------+
                                          v
                          +---------------------------+
                          |   Alert Router              |
                          |   alert_dispatcher.py        |
                          +--------------+---------------+
                                        |
                          +-------------+-------------+
                          v                           v
                +------------------+       +----------------------+
                |  Telegram alert   |       |  triggers.log.jsonl   |
                +------------------+       +-----------+-----------+
                                                        v
   +---------------------------+           +---------------------------+
   |  scanner_manager.py         |<--------  |   dashboard.py             |
   |  start/stop scanners as     |  controls |   (Live / Analytics /       |
   |  background processes       |           |    Scanners / Settings)     |
   +--------------+--------------+           +--------------+--------------+
                  |                                          |
                  v                                          v
   +---------------------------+           +-----------------------------+
   |  scanner_watchdog.py         |         |  trigger_stats.py             |
   |  heartbeat + liveness check,  |         |  per-rule firing frequency,   |
   |  auto-restart + Telegram      |         |  feeds the Analytics tab      |
   |  alert on a dead/hung scanner |         +-----------------------------+
   +---------------------------+
                                                           |
                          +--------------------------------+
                          v
              +-----------------------------------------------+
              |  config_store.py (.env + config.json)           |
              |  Bet Logger + CLV Engine (clv_engine.py)         |
              |  - log_bet.py CLI: log_bet(trigger_rule=...) /   |
              |    closing line / record_outcome()                |
              |  - summary_by_rule(): per-rule CLV, feeds the     |
              |    Analytics tab                                    |
              +-----------------------------------------------+
```

Two independent polling loops (one per sport) hit their data adapters,
run every registered rule against fresh game state, and hand off any
fired `TriggerEvent` to `AlertRouter`, which sends it to Telegram and
appends it to a JSONL log that `dashboard.py` tails. No message queue
or database server - a flat file and SQLite are enough at the scale of
"a few dozen concurrent games."

**Soccer's polling is window-aware, not a flat interval** - neither
soccer trigger can fire outside its own rule window (0-20min for
red-card/early-concede, ~75min+ for late pressure), so `run_soccer.py`
doesn't poll at all in the ~55-minute dead zone between them, and only
fetches the one endpoint each window's rule actually needs (events for
window 1, statistics for window 2, never both). This isn't a
micro-optimization: the naive flat-60s-interval version cost ~270
requests per match against a 100/day free-tier cap - it would exhaust
its quota partway through the first live match it tracked. The
windowed version costs ~55/match. See `run_soccer.py`'s module
docstring for the full math.

## Engineering highlights

- **Budget-aware polling, derived from the rules themselves**: soccer
  polling windows aren't a guess - they're mechanically derived from
  each `TriggerRule`'s own minute-based firing conditions, cutting a
  naive design's API cost by ~5x and turning a guaranteed same-day
  quota exhaustion into a design that comfortably tracks a full match.
- **A dead-man's switch, not just a happy path**: `scanner_watchdog.py`
  distinguishes "process alive" from "process actually making
  progress" via a heartbeat file, since a hung-but-alive process looks
  identical to a healthy one under a bare liveness check. Auto-restarts
  and alerts on Telegram, because a monitoring tool that dies silently
  is worse than useless - it creates false confidence.
- **The insight loop actually closes**: bets are tagged with the
  `trigger_rule` that produced them (`clv_engine.BetLogger.
  summary_by_rule()`), so the dashboard can answer "is this specific
  rule worth anything" instead of only an aggregate number that can
  hide a great rule averaging out against a dead one.
- **Adapter pattern**: `PlayByPlayAdapter` is a `Protocol` - swapping
  ESPN for a paid feed means implementing `active_games()`/`poll()`
  against the new source, with zero changes to rule logic or the
  dispatch pipeline.
- **Concurrent polling**: each tick, `TriggerEngine.run()` fans out to
  every active game with `asyncio.gather` - watching 15 games costs one
  round of concurrent requests, not 15 sequential ones - and tolerates
  individual game failures (`return_exceptions=True`) instead of one
  bad response cancelling the whole tick.
- **Real probability math, not a placeholder**: `clv_engine.py`
  implements two de-vig methods (multiplicative, and power - solved via
  bisection search on the exponent) to strip bookmaker margin out of
  odds before comparing prices, which is what makes closing-line-value
  a meaningful signal instead of a vanity number.
- **Validated against live third-party APIs, not just mocks**: both
  adapters were run against real in-progress ESPN/API-Football
  responses during development to catch field-path drift that unit
  tests against static fixtures wouldn't - `api_football_adapter.py`
  correctly handled a fixture with no stats coverage by falling back to
  0 instead of raising.
- **Config-driven, not hardcoded**: every trigger threshold and the
  basketball key-player tags live in `config.json`, editable from a
  Streamlit form (`config_store.py`) - retuning a rule doesn't touch
  code.
- **Background process management from a web UI**: `scanner_manager.py`
  spawns/tracks scanner subprocesses with `psutil`-based liveness
  checks, so starting and stopping them doesn't require a terminal.
- **105 tests, CI on push** (`tests/`, `.github/workflows/tests.yml`)
  covering the de-vig math, every trigger rule's fire/no-fire
  conditions, entity resolution, config persistence, the watchdog's
  dead/hung/healthy decision logic, the soccer scheduler's window math,
  the backtester's real-fixture replay, the classifier's edge-detection
  on synthetic data, and the REST API's every endpoint.
- **A backtesting engine that's honest about what it can't validate**:
  `backtest.py` replays `RedCardStateShiftTrigger` against real
  completed EPL fixtures - not mock data - reconstructing the match
  minute-by-minute from real event timestamps and judging each
  hypothetical fire against the actual final score. It deliberately
  does **not** attempt to backtest `LateCornerCardPressureTrigger`:
  api-football's free events endpoint returns discrete timestamped
  events (goals, cards, subs), not a per-minute shot/corner timeline,
  so there's no way to reconstruct a "shots in the last 10 minutes"
  read after the fact. Documenting a real data-granularity limit
  instead of faking around it is the actual engineering decision here.
- **A statistically-adaptive sibling trigger, running as an A/B test**:
  `LateCornerCardPressureZScoreTrigger` replaces a fixed "3 shots in 10
  minutes" constant with a z-score against the team's *own* shot/corner
  rate earlier in the same match - a team that's been averaging 5
  hitting 3 again isn't a spike, but a team that's been averaging 1
  hitting 3 is, and a fixed threshold can't tell those apart. It runs
  **alongside**, not instead of, the original fixed-threshold version,
  so `summary_by_rule()`'s real usage data can show which style
  actually finds a better edge - an experiment design, not just a
  feature.
- **A classifier layer that refuses to train on insufficient data**:
  `trigger_classifier.py` scores trigger_rule + odds through a logistic
  regression once `MIN_TRAINING_SAMPLES` (20) real settled bets exist -
  below that it returns `None` rather than confidently mis-scoring on a
  handful of rows. Ships with zero real training data; the demo in its
  `__main__` block proves the mechanism against synthetic data instead
  of pretending real results exist yet.
- **A Kelly-fraction bankroll simulator** (`bankroll_sim.py`): turns "a
  rule has +2% CLV" into an actual Monte Carlo distribution of bankroll
  outcomes at half-Kelly staking, including P(ruin) - a measured edge
  alone says nothing about position sizing or realistic variance.
- **Bootstrap confidence intervals on CLV**, not a normal-approximation
  interval - CLV distributions are routinely skewed (a handful of
  extreme-odds bets dominate the tail), and `clv_engine.bootstrap_ci()`
  refuses to report an interval below 8 samples rather than resampling
  3 numbers into a confident-looking but meaningless range.
- **A market-shift snapshot, honestly scoped**: each trigger captures
  pre-match win probability alongside a fresh odds fetch taken the
  instant it fires - the closest direct test of the app's own thesis
  (the market lags a state change). This is explicitly a before/after
  snapshot, not a continuous line-movement chart: polling odds
  throughout a match would reopen the exact request-budget problem the
  first bullet above exists to solve.
- **A decoupled read-only REST API** (`api.py`, FastAPI): a second,
  independent consumer of the same trigger/CLV data, including a
  Prometheus-style `/metrics` endpoint for scanner uptime and
  heartbeat age. Nothing in `dashboard.py` imports it and nothing in it
  imports `dashboard.py` - proof the data layer isn't secretly coupled
  to one particular UI.

## Files

| File | Purpose |
|---|---|
| `entity_resolution.py` | Normalizes team/player names and timestamps across vendors |
| `basketball_scanner.py` | `BonusTrigger` + `FoulTroubleTrigger`, plus the `TriggerEngine` polling loop |
| `soccer_scanner.py` | `RedCardStateShiftTrigger` + `LateCornerCardPressureTrigger` |
| `espn_basketball_adapter.py` | Real `PlayByPlayAdapter` against ESPN's hidden endpoints |
| `api_football_adapter.py` | Real adapter against api-football, with `/odds`-based pre-match probability fetching |
| `run_basketball.py` / `run_soccer.py` | Wires each sport's adapter -> rules -> Telegram -> log, reading config from `config.json` |
| `alert_dispatcher.py` | Discord/Telegram dispatch with per-rule cooldown |
| `alert_log.py` | Appends triggers to a JSONL file |
| `clv_engine.py` | De-vig (multiplicative + power), CLV%, SQLite bet logger |
| `log_bet.py` | CLI for logging bets / closing lines / outcomes into the CLV tracker |
| `config_store.py` | Reads/writes `.env` and `config.json` - the single source of truth for both the dashboard and the scanners |
| `scanner_manager.py` | Starts/stops/monitors scanner subprocesses (including the watchdog) |
| `scanner_watchdog.py` | Dead-man's switch: heartbeat + liveness checks, auto-restart, Telegram alert on failure |
| `trigger_stats.py` | Aggregates `triggers.log.jsonl` into per-rule firing frequency for the Analytics tab |
| `ui_helpers.py` | Dashboard's custom CSS/typography and small HTML components (status badges, metric cards) |
| `backtest.py` | Replays `RedCardStateShiftTrigger` against real completed fixtures - see Engineering highlights for why only this rule |
| `bankroll_sim.py` | Monte Carlo bankroll simulator at fractional-Kelly staking |
| `trigger_classifier.py` | Logistic-regression scoring layer on top of the rule engine, gated on real sample size |
| `api.py` | Read-only REST API + `/metrics` over the same data, decoupled from the dashboard |
| `dashboard.py` | Streamlit app: Live triggers + CLV, Analytics (per-rule breakdown, bankroll simulator, market-shift chart), Scanners control panel, Settings |
| `tests/` | pytest suite (105 tests) |
| `setup.ps1` / `setup.sh` | One-command environment setup |

Every core module also has a runnable demo under `if __name__ ==
"__main__":` using mock data.

## Trigger logic

### Basketball

**BonusTrigger** - fires when both teams' period foul counts cross the
bonus threshold (5 in the NBA per quarter; 7 for NCAA men per half)
while there's still meaningful time left in the period. Once both sides
are in the bonus, every non-shooting foul becomes free points, and
defenses get more cautious near the rim - that combination tends to
hold pace up or push it higher. Target: live period/game total, lean
over.

**FoulTroubleTrigger** - fires when a player you've tagged as a rim
protector or primary scorer picks up 2 fouls in the first quarter (NBA)
or first half (NCAA). You maintain the "load-bearing player" tag
yourself, in the dashboard's Settings tab - no free feed reliably tells
you which players matter to a given team's rotation.

### Soccer

**RedCardStateShiftTrigger** - fires when a pre-match favorite (win
probability above a threshold, derived from de-vigged pre-match odds
fetched automatically from API-Football's `/odds` endpoint) either goes
down to 10 men or concedes before minute 20. Default threshold is
**0.55**, not the more obvious-looking 0.65 - confirmed against real
UCL odds that even clear favorites (Real Madrid ~60%, Man City ~56%)
rarely clear 65% fair win probability once a 3-way market's draw price
is accounted for.

**LateCornerCardPressureTrigger** - from minute 75 on, fires when a
favored team trailing by exactly one goal starts visibly forcing the
issue: a spike in shots or corners over a trailing 10-minute window,
judged against a fixed constant (default: 3 shots or 2 corners).

**LateCornerCardPressureZScoreTrigger** - the same trailing-favorite
condition, but judges the spike against the team's *own* shot/corner
rate earlier in the same match (a z-score) instead of a fixed constant.
Runs alongside the fixed-threshold version, not instead of it, so real
usage data can show which style actually finds a better edge - needs
`min_history_samples` (default 3) prior readings before it will fire at
all, and declines to fire rather than divide by zero against a
zero-variance baseline.

Both rule sets are intentionally simple threshold logic, not models -
fast, auditable, and every constant is tunable from the dashboard
without touching rule internals.

## Data sources

Checked in September 2026. Pricing, rate limits, and free-tier terms
shift often - re-verify before committing to one.

| Source | Sport | Free tier | Notes |
|---|---|---|---|
| **ESPN hidden endpoints** | NBA, NCAA, soccer | Free, unlimited, no key | Undocumented and unofficial. Best free source for box scores and play-by-play. |
| **API-Football / API-Sports** | Soccer | 100 req/day, every endpoint incl. live events and `/odds` | Free tier blocks schedule/season lookups for the current season (works for 2022-2024 only) but the live-fixtures and per-fixture `/odds` endpoints this app actually depends on are unrestricted. |
| **balldontlie** | NBA | 5 req/min free | Clean, documented upgrade path once ESPN's instability becomes a problem. |
| **The Odds API** | Both, odds only | 500 credits/month | Alternative odds source if API-Football's `/odds` coverage is thin for a given match. |

## De-vig and CLV, in plain terms

A sportsbook's odds always sum to slightly more than 100% implied
probability - that gap is the vig. To know whether you actually beat
the closing line, strip the vig out of the closing odds first.

- **Multiplicative**: scales each side's implied probability down
  proportionally. Simple, but overstates the favorite's edge on lopsided lines.
- **Power**: solves for one exponent applied to every implied
  probability so they sum to 1. Tracks real market prices better on
  asymmetric lines - the default in `clv_engine.py`.

`calculate_clv_pct()` takes the odds you took, the full closing market,
and which side you bet, de-vigs the closing line, and returns your edge
versus the fair closing price as a percentage.

## How to actually use it

The system's job stops at "alert you" - it never places a bet.

1. A trigger fires -> you get a Telegram message with a `market_hint`
   (a direction to check, not a price to bet blindly).
2. Check the live line on your own book. If you like the price, place
   it there manually.
3. Log it immediately, tagging which rule prompted it: `python
   log_bet.py new --sport EPL --game-id <id> --market moneyline
   --selection "..." --stake 1 --odds 4.20 --trigger-rule
   red_card_state_shift`
4. Near the game's close, record the full closing market:
   `python log_bet.py close <bet_id> --closing-odds 4.05,1.72,4.30
   --index 0`
5. After the game: `python log_bet.py outcome <bet_id> win`
6. Check `python log_bet.py summary --by-rule` or the dashboard's
   **Analytics** tab. Track average CLV **per trigger rule**, not just
   overall - a rule with negative CLV over a real sample isn't finding
   an edge and should be retuned or retired. The Analytics tab also
   shows firing frequency per rule - a rule that's fired zero times is
   either miscalibrated or genuinely rare, and you can't tell which
   without counting.

## Testing

```bash
.venv\Scripts\pytest tests/ -v      # Windows
.venv/bin/pytest tests/ -v          # macOS / Linux
```

105 tests covering de-vig math, every trigger rule's fire/no-fire
conditions (including the z-score sibling's edge cases - zero-variance
baselines, insufficient history), entity resolution, config
persistence, the watchdog's dead/hung/healthy decisions, the soccer
scheduler's window math, the backtester's real-fixture replay logic,
the classifier's sample-size gating, and every REST API endpoint via
FastAPI's TestClient. CI runs this on every push via
`.github/workflows/tests.yml`.

## Deploying so it runs when your PC is off

Everything above runs only while your machine is on and the dashboard
process is alive. For always-on operation:

- **Windows Task Scheduler**: schedule `dashboard.py` (or the scanners
  directly) to start at login/boot - free, but still tied to your PC
  being on.
- **A small VPS** ($5-6/mo, DigitalOcean/Hetzner/Linode): run
  `docker compose up -d`, or run each scanner as a `systemd` service.
  True 24/7 operation independent of your laptop.
- Either way, log-rotate `triggers.log.jsonl` (a `logrotate` config or a
  weekly cron job) so it doesn't grow unbounded.

## Roadmap

**Done**: mock-data validation for all rules, real adapter validation
against live ESPN/API-Football data, automatic pre-match odds fetching
and de-vig, budget-aware adaptive polling for soccer, a dead-man's-
switch watchdog with auto-restart, a config-driven dashboard with
process control, per-rule CLV/win-rate breakdown and trigger-frequency
analytics, a real-fixture backtesting engine (for the one rule the free
data source can actually validate), a statistically-adaptive sibling
trigger running as a live A/B test, bootstrap confidence intervals on
CLV, a Kelly-fraction bankroll simulator, a market-shift snapshot at
trigger-time, a decoupled REST API with Prometheus-style metrics, a
classifier scoring layer (infrastructure-complete, awaiting real
training data), and a 105-test pytest/CI suite.

**Next**:
- Validate `espn_basketball_adapter.py` against a real live game (blocked until NBA preseason, Oct 2026)
- Automate closing-line snapshots so `record_closing_line()` doesn't need a manual call per bet
- Once `trigger_classifier.py` has 20+ real settled bets per rule, wire its score into the dashboard's trigger display instead of only the raw fire/no-fire signal
- Multi-bookmaker consensus for pre-match odds instead of the first complete market found
- A bet-logging form in the dashboard itself - `log_bet.py` is currently the one workflow still stuck in a terminal, which breaks the app's own "no terminal needed" promise exactly where it matters most
