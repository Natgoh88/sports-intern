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

import pandas as pd
import streamlit as st

from clv_engine import BetLogger
import config_store
import scanner_manager
import trigger_stats
import ui_helpers

st.set_page_config(page_title="Sports Intern Dashboard", layout="wide")
st.markdown(ui_helpers.CSS, unsafe_allow_html=True)
st.markdown(ui_helpers.header_html("Sports Intern", "Live triggers &middot; CLV tracker"), unsafe_allow_html=True)

TRIGGER_LOG_PATH = os.environ.get("TRIGGER_LOG_PATH", "triggers.log.jsonl")
BETS_DB_PATH = os.environ.get("BETS_DB_PATH", "bets.db")


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
            st.info(f"No bet log found at {BETS_DB_PATH}. Log a bet with `python log_bet.py new ...`.")

    st.caption("Click Rerun (top right, or press R) for the latest data.")

# ---------------------------------------------------------------------------
# Analytics tab
# ---------------------------------------------------------------------------
with tab_analytics:
    st.subheader("Per-rule CLV performance")
    st.caption(
        "Overall CLV can hide a great rule and a dead one averaging out to mediocre. This breaks it "
        "down by the trigger_rule tag on each logged bet (pass --trigger-rule to `log_bet.py new`). "
        "A rule with negative avg CLV over a real sample isn't finding an edge - retune or drop it."
    )
    if os.path.exists(BETS_DB_PATH):
        by_rule = BetLogger(db_path=BETS_DB_PATH).summary_by_rule()
        if by_rule:
            rule_df = pd.DataFrame(
                [
                    {
                        "rule": rule,
                        "total_bets": s["total_bets"],
                        "settled": s["settled_bets"],
                        "win_rate": f"{s['win_rate']:.1%}" if s["win_rate"] is not None else "n/a",
                        "avg_clv_pct": f"{s['avg_clv_pct']:+.2f}%" if s["avg_clv_pct"] is not None else "n/a",
                        "net_units": f"{s['net_units']:+.2f}",
                    }
                    for rule, s in sorted(by_rule.items())
                ]
            )
            st.dataframe(rule_df, use_container_width=True, hide_index=True)
            if "untagged" in by_rule:
                st.caption(
                    "'untagged' = bets logged without --trigger-rule. Tag new bets going forward to "
                    "get a real per-rule breakdown."
                )
        else:
            st.info("No bets logged yet.")
    else:
        st.info(f"No bet log found at {BETS_DB_PATH}. Log a bet with `python log_bet.py new ...`.")

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
