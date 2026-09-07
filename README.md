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

*(Add real screenshots here before publishing - the dashboard's three
tabs are exactly what a reader will want to see first.)*

```
docs/screenshot-live.png       - Live tab: recent triggers + CLV panel
docs/screenshot-scanners.png   - Scanners tab: start/stop controls
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
                         |  - API-Football              |
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
   |  run_basketball.py         |                   |  (odds fetch + de-vig,     |
   |                             |                   |   EPL/UCL filter)          |
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
   |  start/stop scanners as     |  controls |   (Live / Scanners /        |
   |  background processes       |           |    Settings tabs)           |
   +---------------------------+           +--------------+--------------+
                                                           |
                          +--------------------------------+
                          v
              +-----------------------------------------------+
              |  config_store.py (.env + config.json)           |
              |  Bet Logger + CLV Engine (clv_engine.py)         |
              |  - log_bet.py CLI: log_bet() / closing line /    |
              |    record_outcome()                               |
              +-----------------------------------------------+
```

Two independent polling loops (one per sport) hit their data adapters
on an interval, run every registered rule against fresh game state, and
hand off any fired `TriggerEvent` to `AlertRouter`, which sends it to
Telegram and appends it to a JSONL log that `dashboard.py` tails. No
message queue or database server - a flat file and SQLite are enough at
the scale of "a few dozen concurrent games."

## Engineering highlights

- **Adapter pattern**: `PlayByPlayAdapter` is a `Protocol` - swapping
  ESPN for a paid feed means implementing `active_games()`/`poll()`
  against the new source, with zero changes to rule logic or the
  dispatch pipeline.
- **Concurrent polling**: each tick, `TriggerEngine.run()` fans out to
  every active game with `asyncio.gather` - watching 15 games costs one
  round of concurrent requests, not 15 sequential ones.
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
- **33 tests, CI on push** (`tests/`, `.github/workflows/tests.yml`)
  covering the de-vig math, every trigger rule's fire/no-fire
  conditions, entity resolution, and config persistence.

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
| `scanner_manager.py` | Starts/stops/monitors scanner subprocesses |
| `dashboard.py` | Streamlit app: Live triggers + CLV, Scanners control panel, Settings |
| `tests/` | pytest suite (33 tests) |
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
issue: a spike in shots or corners over a trailing 10-minute window.

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
3. Log it immediately: `python log_bet.py new --sport EPL --game-id
   <id> --market moneyline --selection "..." --stake 1 --odds 4.20`
4. Near the game's close, record the full closing market:
   `python log_bet.py close <bet_id> --closing-odds 4.05,1.72,4.30
   --index 0`
5. After the game: `python log_bet.py outcome <bet_id> win`
6. Check `python log_bet.py summary` or the dashboard's CLV panel.
   Track average CLV **per trigger rule**, not just overall - a rule
   with negative CLV over a real sample isn't finding an edge and
   should be retuned or retired.

## Testing

```bash
.venv\Scripts\pytest tests/ -v      # Windows
.venv/bin/pytest tests/ -v          # macOS / Linux
```

33 tests covering de-vig math, every trigger rule's fire/no-fire
conditions, entity resolution, and config persistence. CI runs this on
every push via `.github/workflows/tests.yml`.

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
and de-vig, a config-driven dashboard with process control, and a
pytest/CI suite.

**Next**:
- Validate `espn_basketball_adapter.py` against a real live game (blocked until NBA preseason, Oct 2026)
- Automate closing-line snapshots so `record_closing_line()` doesn't need a manual call per bet
- Backtest each trigger rule's average CLV over a full season and retire/retune whatever doesn't hold up
