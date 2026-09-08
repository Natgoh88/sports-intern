"""
dashboard.py

The app's main interface. Four tabs:

- Live: recent triggers (from triggers.log.jsonl) + overall CLV
  performance (from bets.db via clv_engine.BetLogger).
- Analytics: the actual "is this working" view - per-rule CLV/win-rate
  breakdown (clv_engine.BetLogger.summary_by_rule()) and trigger firing
  frequency (trigger_stats.py). A rule with negative CLV over a real
  sample, or one that's fired zero times, is a rule to retune or drop -
  see README's "How to actually use it" section.
- Scanners: start/stop the basketball and soccer scanners (and the
  watchdog) as background processes (scanner_manager.py) instead of
  running them from a terminal.
- Settings: edit .env (Telegram/API-Football credentials) and
  config.json (trigger thresholds, basketball key players) from a form
  instead of hand-editing files.

Run:
    streamlit run dashboard.py
"""

import asyncio
import json
import os
import time

import pandas as pd
import streamlit as st

from clv_engine import BetLogger, MIN_SAMPLES_FOR_CI
from basketball_scanner import BonusTrigger, FoulTroubleTrigger
from soccer_scanner import RedCardStateShiftTrigger, LateCornerCardPressureTrigger, LateCornerCardPressureZScoreTrigger
import bankroll_sim
import config_store
import scanner_manager
import trigger_classifier
import trigger_stats
import ui_helpers

# pulled from the actual rule classes, not duplicated as magic strings -
# stays in sync automatically if a rule is renamed or added
KNOWN_RULE_NAMES = [
    BonusTrigger.name,
    FoulTroubleTrigger.name,
    RedCardStateShiftTrigger.name,
    LateCornerCardPressureTrigger.name,
    LateCornerCardPressureZScoreTrigger.name,
]

st.set_page_config(page_title="Sports Intern Dashboard", layout="wide")
st.markdown(ui_helpers.CSS, unsafe_allow_html=True)

TRIGGER_LOG_PATH = os.environ.get("TRIGGER_LOG_PATH", "triggers.log.jsonl")
BETS_DB_PATH = os.environ.get("BETS_DB_PATH", "bets.db")


def _time_ago(seconds: float) -> str:
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _live_status_text() -> tuple[str, bool]:
    """Builds the header's status line from actual current state -
    running scanner count and time since the last real trigger -
    instead of static copy."""
    running = [name for name in ("soccer", "basketball") if scanner_manager.status(name)["running"]]
    trigger_rows = trigger_stats.load_trigger_rows(TRIGGER_LOG_PATH)

    if running:
        scanner_part = f"{len(running)} SCANNER{'S' if len(running) != 1 else ''} ACTIVE ({', '.join(running).upper()})"
    else:
        scanner_part = "NO SCANNERS RUNNING"

    if trigger_rows:
        last_fired = max(r.get("fired_at", 0) for r in trigger_rows)
        trigger_part = f"LAST TRIGGER {_time_ago(time.time() - last_fired).upper()}"
    else:
        trigger_part = "NO TRIGGERS YET"

    return f"{scanner_part} &mdash; {trigger_part}", bool(running)


_status_text, _is_live = _live_status_text()
st.markdown(ui_helpers.header_html("Sports Intern", _status_text, _is_live), unsafe_allow_html=True)


def _load_trigger_df() -> pd.DataFrame | None:
    rows = trigger_stats.load_trigger_rows(TRIGGER_LOG_PATH)
    if not rows:
        return None
    return pd.DataFrame(rows).sort_values("fired_at", ascending=False)


tab_live, tab_analytics, tab_scanners, tab_settings = st.tabs(["Live", "Analytics", "Scanners", "Settings"])

# ---------------------------------------------------------------------------
# Live tab
# ---------------------------------------------------------------------------
with tab_live:
    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("Recent triggers")
        df = _load_trigger_df()
        if df is not None:
            st.dataframe(df[["game_id", "rule_name", "message", "market_hint"]], use_container_width=True)
        else:
            st.info(f"No trigger log found at {TRIGGER_LOG_PATH}. Start a scanner from the Scanners tab.")

    with col2:
        st.subheader("CLV performance")
        if os.path.exists(BETS_DB_PATH):
            summary = BetLogger(db_path=BETS_DB_PATH).summary()
            st.markdown(ui_helpers.metric_card_html("Total bets", str(summary["total_bets"])), unsafe_allow_html=True)
            st.markdown(ui_helpers.metric_card_html("Settled", str(summary["settled_bets"])), unsafe_allow_html=True)
            if summary["win_rate"] is not None:
                st.markdown(ui_helpers.metric_card_html("Win rate", f"{summary['win_rate']:.1%}"), unsafe_allow_html=True)
            if summary["avg_clv_pct"] is not None:
                clv = summary["avg_clv_pct"]
                st.markdown(
                    ui_helpers.metric_card_html("Avg CLV", f"{clv:+.2f}%", "positive" if clv >= 0 else "negative"),
                    unsafe_allow_html=True,
                )
            units = summary["net_units"]
            st.markdown(
                ui_helpers.metric_card_html("Net units", f"{units:+.2f}", "positive" if units >= 0 else "negative"),
                unsafe_allow_html=True,
            )
        else:
            st.info("No bet log found yet. Log your first bet below.")

    st.caption("Click Rerun (top right, or press R) for the latest data.")

    st.divider()
    st.subheader("Log a bet")
    st.caption(
        "Acted on an alert? Log it here instead of the log_bet.py CLI - tagging the trigger rule is "
        "what powers the Analytics tab's per-rule breakdown, the bankroll simulator's real-data "
        "seeding, and (once there's enough of it) the classifier."
    )

    with st.form("new_bet_form", clear_on_submit=True):
        nb_c1, nb_c2 = st.columns(2)
        with nb_c1:
            nb_sport = st.text_input("Sport", placeholder="EPL, UCL, NBA, NCAAM...")
            nb_game_id = st.text_input("Game ID", placeholder="from the Telegram alert / triggers.log.jsonl")
            nb_market = st.text_input("Market", placeholder="moneyline, total, spread, corners_over_9.5...")
        with nb_c2:
            nb_selection = st.text_input("Selection", placeholder='e.g. "Bournemouth ML", "Over 54.5"')
            nb_stake = st.number_input("Stake", min_value=0.01, value=1.0, step=0.5)
            nb_odds = st.number_input("Decimal odds taken", min_value=1.01, value=1.91, step=0.01)
        nb_rule = st.selectbox("Trigger rule (optional, but tag it if you can)", options=["(untagged)"] + KNOWN_RULE_NAMES)
        nb_submitted = st.form_submit_button("Log bet")

    if nb_submitted:
        if not (nb_sport and nb_game_id and nb_market and nb_selection):
            st.error("Sport, Game ID, Market, and Selection are all required.")
        else:
            logger = BetLogger(db_path=BETS_DB_PATH)
            bet_id = logger.log_bet(
                sport=nb_sport,
                game_id=nb_game_id,
                market=nb_market,
                selection=nb_selection,
                stake=nb_stake,
                odds_taken=nb_odds,
                trigger_rule=None if nb_rule == "(untagged)" else nb_rule,
            )
            st.success(f"Logged bet {bet_id[:8]}...")
            st.rerun()

    st.markdown("#### Open bets")
    st.caption("Bets with no recorded outcome yet. Record the closing line near kickoff/game-close for a real CLV number, and the outcome once the game finishes.")

    if os.path.exists(BETS_DB_PATH):
        open_bets = BetLogger(db_path=BETS_DB_PATH).list_open_bets()
        if not open_bets:
            st.caption("No open bets.")
        for bet in open_bets:
            has_closing_line = bet["closing_odds_market"] is not None
            label = f"{bet['sport']} | {bet['selection']} @ {bet['odds_taken']:.2f} | {_time_ago(time.time() - bet['placed_at'])}"
            with st.expander(label):
                st.caption(
                    f"Game ID: `{bet['game_id']}` | Market: {bet['market']} | Stake: {bet['stake']} | "
                    f"Rule: {bet['trigger_rule'] or 'untagged'}"
                )

                if not has_closing_line:
                    with st.form(f"close_form_{bet['bet_id']}"):
                        st.caption("Record the closing market (all outcomes, comma-separated) to compute real CLV.")
                        close_c1, close_c2 = st.columns(2)
                        with close_c1:
                            closing_odds_str = st.text_input("Closing odds, comma-separated", placeholder="e.g. 1.87,2.02", key=f"co_{bet['bet_id']}")
                        with close_c2:
                            selection_index = st.number_input("Index of your side in that list", min_value=0, value=0, key=f"idx_{bet['bet_id']}")
                        if st.form_submit_button("Record closing line"):
                            try:
                                closing_odds = [float(x.strip()) for x in closing_odds_str.split(",") if x.strip()]
                                if not closing_odds:
                                    raise ValueError("enter at least one closing price")
                                clv = BetLogger(db_path=BETS_DB_PATH).record_closing_line(bet["bet_id"], closing_odds, int(selection_index))
                                st.success(f"CLV: {clv:+.2f}%")
                                st.rerun()
                            except Exception as exc:
                                st.error(f"Couldn't record that: {exc}")
                else:
                    st.caption("Closing line already recorded.")

                st.caption("Outcome:")
                out_c1, out_c2, out_c3 = st.columns(3)
                with out_c1:
                    if st.button("Win", key=f"win_{bet['bet_id']}"):
                        BetLogger(db_path=BETS_DB_PATH).record_outcome(bet["bet_id"], "win")
                        st.rerun()
                with out_c2:
                    if st.button("Loss", key=f"loss_{bet['bet_id']}"):
                        BetLogger(db_path=BETS_DB_PATH).record_outcome(bet["bet_id"], "loss")
                        st.rerun()
                with out_c3:
                    if st.button("Push", key=f"push_{bet['bet_id']}"):
                        BetLogger(db_path=BETS_DB_PATH).record_outcome(bet["bet_id"], "push")
                        st.rerun()

# ---------------------------------------------------------------------------
# Analytics tab
# ---------------------------------------------------------------------------
with tab_analytics:
    st.subheader("Per-rule CLV performance")
    st.caption(
        "Overall CLV can hide a great rule and a dead one averaging out to mediocre. This breaks it "
        "down by the trigger_rule tag on each logged bet (log it from the Live tab, or pass "
        "--trigger-rule to `log_bet.py new`). A rule with negative avg CLV over a real sample isn't "
        f"finding an edge - retune or drop it. The 95% CI column only appears at {MIN_SAMPLES_FOR_CI}+ "
        "settled bets with a recorded closing line - below that, an interval would just be a "
        f"confident-looking number built from noise. Classifier P(win) needs {trigger_classifier.MIN_TRAINING_SAMPLES}+ "
        "settled bets across ALL rules combined before it trains at all (see trigger_classifier.py) - "
        "it's the model's predicted win probability for that rule at its own average odds, not a "
        "guess dressed up as a number."
    )
    if os.path.exists(BETS_DB_PATH):
        by_rule = BetLogger(db_path=BETS_DB_PATH).summary_by_rule()
        trained_model = trigger_classifier.train(db_path=BETS_DB_PATH)
        if by_rule:
            rule_df = pd.DataFrame(
                [
                    {
                        "rule": rule,
                        "total_bets": s["total_bets"],
                        "settled": s["settled_bets"],
                        "win_rate": f"{s['win_rate']:.1%}" if s["win_rate"] is not None else "n/a",
                        "avg_clv_pct": f"{s['avg_clv_pct']:+.2f}%" if s["avg_clv_pct"] is not None else "n/a",
                        "95% CI": (
                            f"[{s['avg_clv_ci95'][0]:+.2f}%, {s['avg_clv_ci95'][1]:+.2f}%]"
                            if s["avg_clv_ci95"] is not None
                            else f"n={s['avg_clv_n']}, need {MIN_SAMPLES_FOR_CI - s['avg_clv_n']} more"
                        ),
                        "classifier P(win)": (
                            f"{trigger_classifier.score(trained_model, rule, s['avg_odds_taken']):.1%}"
                            if trained_model is not None and s["avg_odds_taken"] is not None
                            else "not trained yet"
                        ),
                        "net_units": f"{s['net_units']:+.2f}",
                    }
                    for rule, s in sorted(by_rule.items())
                ]
            )
            st.dataframe(rule_df, use_container_width=True, hide_index=True)
            if "untagged" in by_rule:
                st.caption(
                    "'untagged' = bets logged without a trigger rule tagged. Tag new bets going "
                    "forward to get a real per-rule breakdown."
                )
        else:
            st.info("No bets logged yet.")
    else:
        st.info("No bet log found yet. Log a bet from the Live tab.")

    st.divider()
    st.subheader("Trigger frequency")
    st.caption(
        "How often each rule has actually fired. A rule at zero after real playing time is either "
        "miscalibrated (check thresholds in Settings) or the condition is genuinely rare - a rule "
        "firing constantly is the opposite problem: too loose to be useful signal."
    )
    rows = trigger_stats.load_trigger_rows(TRIGGER_LOG_PATH)
    freq = trigger_stats.frequency_by_rule(rows)
    if freq:
        freq_df = pd.DataFrame(
            [
                {
                    "rule": rule,
                    "times_fired": stats["count"],
                    "distinct_games": stats["distinct_games"],
                    "last_fired": (
                        pd.to_datetime(stats["last_fired_at"], unit="s").strftime("%Y-%m-%d %H:%M UTC")
                        if stats["last_fired_at"]
                        else "n/a"
                    ),
                }
                for rule, stats in sorted(freq.items())
            ]
        )
        st.dataframe(freq_df, use_container_width=True, hide_index=True)
    else:
        st.info("No triggers logged yet - nothing to summarize.")

    st.divider()
    st.subheader("Bankroll simulator")
    st.caption(
        "A measured edge alone doesn't tell you how much to bet or what the realistic variance "
        "around it looks like. This runs a Monte Carlo simulation of bankroll outcomes at "
        "fractional-Kelly staking (see bankroll_sim.py for why half-Kelly, not full, is the default) "
        "for a chosen win probability and odds - defaulting to a real rule's numbers when it has "
        f"enough settled bets ({MIN_SAMPLES_FOR_CI}+), otherwise plug in hypothetical numbers."
    )

    rule_options = {"(manual entry)": None}
    if os.path.exists(BETS_DB_PATH):
        for rule, s in BetLogger(db_path=BETS_DB_PATH).summary_by_rule().items():
            if s["win_rate"] is not None and s["avg_odds_taken"] is not None:
                rule_options[rule] = s

    selected_rule = st.selectbox("Seed from a rule's real numbers", options=list(rule_options.keys()))
    seed = rule_options[selected_rule]
    if seed is not None and seed["settled_bets"] < MIN_SAMPLES_FOR_CI:
        st.warning(f"Only {seed['settled_bets']} settled bet(s) for this rule - numbers below are a rough seed, not a validated edge.")

    sim_c1, sim_c2, sim_c3 = st.columns(3)
    with sim_c1:
        sim_win_prob = st.slider("Win probability", 0.01, 0.99, value=seed["win_rate"] if seed else 0.55, key="sim_win_prob")
    with sim_c2:
        sim_odds = st.number_input("Decimal odds", min_value=1.01, value=seed["avg_odds_taken"] if seed else 1.91, key="sim_odds")
    with sim_c3:
        sim_kelly_mult = st.slider("Kelly fraction (0.5 = half-Kelly)", 0.05, 1.0, value=0.5, key="sim_kelly_mult")

    sim_n_bets = st.slider("Number of future bets to simulate", 10, 500, value=100, key="sim_n_bets")

    if st.button("Run simulation"):
        result = bankroll_sim.simulate(
            win_prob=sim_win_prob,
            decimal_odds=sim_odds,
            n_bets=sim_n_bets,
            kelly_fraction_multiplier=sim_kelly_mult,
        )
        implied_edge = sim_win_prob * sim_odds - 1
        if implied_edge <= 0:
            st.error(f"No edge at these numbers (implied edge {implied_edge:+.1%}) - Kelly stakes 0% of bankroll. Nothing to simulate.")
        else:
            st.markdown(
                ui_helpers.metric_card_html("Kelly stake per bet", f"{result.kelly_fraction_used:.1%} of bankroll"),
                unsafe_allow_html=True,
            )
            rc1, rc2, rc3 = st.columns(3)
            with rc1:
                st.markdown(ui_helpers.metric_card_html("5th percentile", f"{result.percentile(5):.0f}"), unsafe_allow_html=True)
            with rc2:
                st.markdown(ui_helpers.metric_card_html("Median outcome", f"{result.percentile(50):.0f}"), unsafe_allow_html=True)
            with rc3:
                st.markdown(ui_helpers.metric_card_html("95th percentile", f"{result.percentile(95):.0f}"), unsafe_allow_html=True)
            ruin_sentiment = "negative" if result.prob_of_ruin > 0.05 else "neutral"
            st.markdown(
                ui_helpers.metric_card_html("P(ruin) - ending below 20% of start", f"{result.prob_of_ruin:.1%}", ruin_sentiment),
                unsafe_allow_html=True,
            )
            st.caption(f"Starting bankroll normalized to {result.starting_bankroll:.0f} units, {result.n_simulations} simulated paths of {sim_n_bets} bets each.")

    st.divider()
    st.subheader("Market shift at trigger time")
    st.caption(
        "Pre-match win probability vs. a fresh odds snapshot taken the instant a trigger fires - "
        "the closest this app gets to directly testing its own core thesis (the market lags a state "
        "change for a few minutes) with real numbers. This is a before/after snapshot, not a "
        "continuous line-movement chart - polling odds continuously through a match would reopen "
        "the exact request-budget problem task 1 fixed, so only these two points are captured."
    )
    shift_rows = [
        r
        for r in trigger_stats.load_trigger_rows(TRIGGER_LOG_PATH)
        if r.get("metadata", {}).get("odds_pre_match") and r.get("metadata", {}).get("odds_at_trigger")
    ]
    if shift_rows:
        chart_rows = []
        for r in shift_rows:
            pre, at = r["metadata"]["odds_pre_match"], r["metadata"]["odds_at_trigger"]
            for team in pre:
                if team in at:
                    chart_rows.append({"trigger": f"{r['rule_name']} ({team})", "snapshot": "pre-match", "win_prob": pre[team]})
                    chart_rows.append({"trigger": f"{r['rule_name']} ({team})", "snapshot": "at-trigger", "win_prob": at[team]})
        if chart_rows:
            import altair as alt

            shift_chart_df = pd.DataFrame(chart_rows)
            chart = (
                alt.Chart(shift_chart_df)
                .mark_bar()
                .encode(
                    x=alt.X("snapshot:N", title=None, sort=["pre-match", "at-trigger"]),
                    y=alt.Y("win_prob:Q", title="Win probability", scale=alt.Scale(domain=[0, 1])),
                    color=alt.Color(
                        "snapshot:N",
                        scale=alt.Scale(domain=["pre-match", "at-trigger"], range=["#4B5262", "#FFB020"]),
                        legend=alt.Legend(title=None),
                    ),
                    column=alt.Column("trigger:N", title=None),
                    tooltip=["trigger", "snapshot", "win_prob"],
                )
                .properties(width=120)
            )
            st.altair_chart(chart, use_container_width=False)
        else:
            st.info("Snapshots exist but no team names matched between them - nothing to chart.")
    else:
        st.info("No triggers with a captured market-shift snapshot yet - this populates automatically once a real trigger fires live.")

# ---------------------------------------------------------------------------
# Scanners tab
# ---------------------------------------------------------------------------
with tab_scanners:
    st.subheader("Basketball & soccer scanners")
    st.caption(
        "Starts/stops each scanner as a background process on this machine - no terminal needed. "
        "Once started, a scanner keeps running independently of this dashboard (it's a separate "
        "process); it stops only if you stop it here, the PC turns off or sleeps, or - despite the "
        "watchdog below - it crashes in a way that can't self-heal."
    )

    for name, label, caveat in [
        ("soccer", "Soccer (EPL / UCL)", None),
        ("basketball", "Basketball (NBA)", "Untested against a live game - see README."),
    ]:
        st.markdown(f"#### {label}")
        st_status = scanner_manager.status(name)
        c1, c2, c3 = st.columns([2, 1, 3])
        with c1:
            st.markdown(ui_helpers.status_badge_html(st_status["running"], st_status["pid"]), unsafe_allow_html=True)
        with c2:
            if st_status["running"]:
                if st.button("Stop", key=f"stop_{name}"):
                    scanner_manager.stop(name)
                    st.rerun()
            else:
                if st.button("Start", key=f"start_{name}"):
                    scanner_manager.start(name)
                    st.rerun()
        with c3:
            if caveat:
                st.warning(caveat)

        with st.expander(f"{label} recent log output"):
            log_text = scanner_manager.recent_log(name)
            st.code(log_text or "(no output yet)", language="text")

    st.markdown("#### Watchdog")
    st.caption(
        "Auto-starts with either scanner above. Checks every 60s that each running scanner is both "
        "alive and making progress (not just alive but hung), and restarts + alerts you on Telegram "
        "if not. You shouldn't normally need to touch this."
    )
    wd_status = scanner_manager.status("watchdog")
    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown(ui_helpers.status_badge_html(wd_status["running"], wd_status["pid"]), unsafe_allow_html=True)
    with c2:
        if wd_status["running"]:
            if st.button("Stop", key="stop_watchdog"):
                scanner_manager.stop("watchdog")
                st.rerun()
        else:
            if st.button("Start", key="start_watchdog"):
                scanner_manager.start("watchdog")
                st.rerun()
    with st.expander("Watchdog recent log output"):
        st.code(scanner_manager.recent_log("watchdog") or "(no output yet)", language="text")

    st.markdown("#### API server")
    st.caption(
        "Read-only REST API over this same trigger/CLV data (api.py), independent of this dashboard - "
        "GET /health, /triggers, /bets/summary, /bets/summary-by-rule, and a Prometheus-style /metrics. "
        "Runs on port 8000. Interactive docs at /docs once started."
    )
    api_status = scanner_manager.status("api")
    c1, c2 = st.columns([2, 1])
    with c1:
        st.markdown(ui_helpers.status_badge_html(api_status["running"], api_status["pid"]), unsafe_allow_html=True)
    with c2:
        if api_status["running"]:
            if st.button("Stop", key="stop_api"):
                scanner_manager.stop("api")
                st.rerun()
        else:
            if st.button("Start", key="start_api"):
                scanner_manager.start("api")
                st.rerun()
    with st.expander("API server recent log output"):
        st.code(scanner_manager.recent_log("api") or "(no output yet)", language="text")

# ---------------------------------------------------------------------------
# Settings tab
# ---------------------------------------------------------------------------
with tab_settings:
    st.subheader("Credentials")
    st.caption("Saved to .env. Scanners pick up changes on their next start (stop + start after saving).")

    env = config_store.load_env()
    with st.form("env_form"):
        telegram_token = st.text_input("Telegram bot token", value=env["TELEGRAM_BOT_TOKEN"], type="password")
        telegram_chat_id = st.text_input("Telegram chat ID", value=env["TELEGRAM_CHAT_ID"])
        discord_webhook = st.text_input("Discord webhook URL (optional)", value=env["DISCORD_WEBHOOK_URL"])
        api_football_key = st.text_input("API-Football key", value=env["API_FOOTBALL_KEY"], type="password")
        save_env_clicked = st.form_submit_button("Save credentials")

    if save_env_clicked:
        config_store.save_env(
            {
                "TELEGRAM_BOT_TOKEN": telegram_token,
                "TELEGRAM_CHAT_ID": telegram_chat_id,
                "DISCORD_WEBHOOK_URL": discord_webhook,
                "API_FOOTBALL_KEY": api_football_key,
            }
        )
        st.success("Saved to .env.")

    col_test_tg, col_test_api = st.columns(2)
    with col_test_tg:
        if st.button("Send Telegram test message"):
            from alert_dispatcher import TelegramDispatcher, AlertMessage

            async def _send_test():
                dispatcher = TelegramDispatcher(bot_token=telegram_token, chat_id=telegram_chat_id)
                await dispatcher.send(
                    AlertMessage(
                        game_id="dashboard-test",
                        sport="SYSTEM",
                        rule_name="settings_test",
                        detail="Test message from the Settings tab - Telegram is working.",
                        market_hint="n/a",
                        score_line="n/a",
                        game_clock="n/a",
                    )
                )

            try:
                asyncio.run(_send_test())
                st.success("Sent. Check Telegram.")
            except Exception as exc:
                st.error(f"Failed: {exc}")

    with col_test_api:
        if st.button("Check API-Football key"):
            import aiohttp

            async def _check_key():
                async with aiohttp.ClientSession(headers={"x-apisports-key": api_football_key}) as session:
                    async with session.get("https://v3.football.api-sports.io/status") as resp:
                        return await resp.json()

            try:
                data = asyncio.run(_check_key())
                if data.get("errors"):
                    st.error(data["errors"])
                else:
                    req = data["response"]["requests"]
                    st.success(f"Key OK. {req['current']}/{req['limit_day']} requests used today.")
            except Exception as exc:
                st.error(f"Failed: {exc}")

    st.divider()
    st.subheader("Trigger thresholds")
    cfg = config_store.load_config()

    with st.form("thresholds_form"):
        st.markdown("**Basketball**")
        b_poll = st.number_input("Poll interval (seconds)", value=cfg["basketball"]["poll_interval_seconds"], min_value=5, key="b_poll")
        b_bonus_seconds = st.number_input(
            "BonusTrigger: min seconds remaining in period", value=cfg["basketball"]["bonus_trigger"]["min_seconds_remaining"], min_value=0, key="b_bonus"
        )
        b_foul_threshold = st.number_input(
            "FoulTroubleTrigger: foul count threshold", value=cfg["basketball"]["foul_trouble_trigger"]["foul_count_threshold"], min_value=1, key="b_foul_ct"
        )
        b_foul_period_cutoff = st.number_input(
            "FoulTroubleTrigger: only fires at/before this period", value=cfg["basketball"]["foul_trouble_trigger"]["early_period_cutoff"], min_value=1, key="b_foul_period"
        )

        st.markdown("**Soccer**")
        st.caption(
            "Adaptive polling, not a flat interval - the scanner only polls a match during the two "
            "windows below, where a rule can actually fire. See README for the free-tier quota math."
        )
        s_hot_poll = st.number_input(
            "Poll interval while inside a window (seconds)", value=cfg["soccer"]["hot_poll_interval_seconds"], min_value=30, key="s_hot_poll"
        )
        s_window1_len = st.number_input(
            "Window 1 length after kickoff (minutes) - covers RedCardStateShiftTrigger",
            value=cfg["soccer"]["window1_wallclock_minutes"], min_value=10, max_value=60, key="s_w1_len",
        )
        s_window2_start = st.number_input(
            "Window 2 start after kickoff (minutes) - covers LateCornerCardPressureTrigger",
            value=cfg["soccer"]["window2_start_offset_minutes"], min_value=60, max_value=110, key="s_w2_start",
        )
        s_window2_end = st.number_input(
            "Window 2 end after kickoff (minutes)",
            value=cfg["soccer"]["window2_end_offset_minutes"], min_value=70, max_value=140, key="s_w2_end",
        )
        s_fav_threshold = st.slider(
            "RedCardStateShiftTrigger: favorite win-prob threshold", 0.0, 1.0, value=cfg["soccer"]["red_card_state_shift"]["favorite_prob_threshold"], key="s_fav"
        )
        s_minute_cutoff = st.number_input(
            "RedCardStateShiftTrigger: only fires before this minute", value=cfg["soccer"]["red_card_state_shift"]["minute_cutoff"], min_value=1, max_value=90, key="s_cutoff"
        )
        s_minute_start = st.number_input(
            "LateCornerCardPressureTrigger: starts watching from this minute", value=cfg["soccer"]["late_pressure_cooker"]["minute_start"], min_value=1, max_value=90, key="s_start"
        )
        s_shot_spike = st.number_input(
            "LateCornerCardPressureTrigger: shot spike threshold (per 10 min)", value=cfg["soccer"]["late_pressure_cooker"]["shot_spike_threshold"], min_value=1, key="s_shot"
        )
        s_corner_spike = st.number_input(
            "LateCornerCardPressureTrigger: corner spike threshold (per 10 min)", value=cfg["soccer"]["late_pressure_cooker"]["corner_spike_threshold"], min_value=1, key="s_corner"
        )

        save_thresholds_clicked = st.form_submit_button("Save thresholds")

    if save_thresholds_clicked:
        cfg["basketball"]["poll_interval_seconds"] = b_poll
        cfg["basketball"]["bonus_trigger"]["min_seconds_remaining"] = b_bonus_seconds
        cfg["basketball"]["foul_trouble_trigger"]["foul_count_threshold"] = b_foul_threshold
        cfg["basketball"]["foul_trouble_trigger"]["early_period_cutoff"] = b_foul_period_cutoff
        cfg["soccer"]["hot_poll_interval_seconds"] = s_hot_poll
        cfg["soccer"]["window1_wallclock_minutes"] = s_window1_len
        cfg["soccer"]["window2_start_offset_minutes"] = s_window2_start
        cfg["soccer"]["window2_end_offset_minutes"] = s_window2_end
        cfg["soccer"]["red_card_state_shift"]["favorite_prob_threshold"] = s_fav_threshold
        cfg["soccer"]["red_card_state_shift"]["minute_cutoff"] = s_minute_cutoff
        cfg["soccer"]["late_pressure_cooker"]["minute_start"] = s_minute_start
        cfg["soccer"]["late_pressure_cooker"]["shot_spike_threshold"] = s_shot_spike
        cfg["soccer"]["late_pressure_cooker"]["corner_spike_threshold"] = s_corner_spike
        config_store.save_config(cfg)
        st.success("Saved to config.json. Restart the scanner(s) from the Scanners tab to apply.")

    st.divider()
    st.subheader("Basketball key players")
    st.caption(
        'Players FoulTroubleTrigger watches for early foul trouble. No free feed reliably identifies '
        '"load-bearing" players - this is a judgment call you maintain yourself (season usage rate / '
        "minutes / defensive role). Match on team abbreviation and exact ESPN athlete displayName."
    )

    key_players = cfg["basketball"]["key_players"]
    kp_rows = [
        {"team": team, "player": player, "role": role}
        for team, players in key_players.items()
        for player, role in players.items()
    ]
    edited = st.data_editor(
        pd.DataFrame(kp_rows, columns=["team", "player", "role"]),
        num_rows="dynamic",
        use_container_width=True,
        key="key_players_editor",
    )

    if st.button("Save key players"):
        new_key_players: dict[str, dict[str, str]] = {}
        for _, row in edited.iterrows():
            team, player, role = row.get("team"), row.get("player"), row.get("role")
            if not team or not player:
                continue
            new_key_players.setdefault(team, {})[player] = role or ""
        cfg["basketball"]["key_players"] = new_key_players
        config_store.save_config(cfg)
        st.success("Saved. Restart the basketball scanner to apply.")
