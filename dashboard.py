"""
dashboard.py

The app's main interface. Three tabs:

- Live: recent triggers (from triggers.log.jsonl) + CLV performance (from
  bets.db via clv_engine.BetLogger) - the original dashboard.
- Scanners: start/stop the basketball and soccer scanners as background
  processes (scanner_manager.py) instead of running them from a terminal.
- Settings: edit .env (Telegram/API-Football credentials) and
  config.json (trigger thresholds, basketball key players) from a form
  instead of hand-editing files. Includes a "send test message" button
  for Telegram and a "check key" button for API-Football so mistakes
  show up immediately instead of during a live game.

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

st.set_page_config(page_title="Sports Intern Dashboard", layout="wide")
st.title("Sports Betting Intern")

TRIGGER_LOG_PATH = os.environ.get("TRIGGER_LOG_PATH", "triggers.log.jsonl")
BETS_DB_PATH = os.environ.get("BETS_DB_PATH", "bets.db")

tab_live, tab_scanners, tab_settings = st.tabs(["Live", "Scanners", "Settings"])

# ---------------------------------------------------------------------------
# Live tab
# ---------------------------------------------------------------------------
with tab_live:
    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("Recent triggers")
        if os.path.exists(TRIGGER_LOG_PATH):
            rows = []
            with open(TRIGGER_LOG_PATH) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
            if rows:
                df = pd.DataFrame(rows).sort_values("fired_at", ascending=False)
                st.dataframe(df[["game_id", "rule_name", "message", "market_hint"]], use_container_width=True)
            else:
                st.info("No triggers logged yet.")
        else:
            st.info(f"No trigger log found at {TRIGGER_LOG_PATH}. Start a scanner from the Scanners tab.")

    with col2:
        st.subheader("CLV performance")
        if os.path.exists(BETS_DB_PATH):
            logger = BetLogger(db_path=BETS_DB_PATH)
            summary = logger.summary()
            st.metric("Total bets", summary["total_bets"])
            st.metric("Settled", summary["settled_bets"])
            if summary["win_rate"] is not None:
                st.metric("Win rate", f"{summary['win_rate']:.1%}")
            if summary["avg_clv_pct"] is not None:
                st.metric("Avg CLV", f"{summary['avg_clv_pct']:.2f}%")
            st.metric("Net units", f"{summary['net_units']:.2f}")
        else:
            st.info(f"No bet log found at {BETS_DB_PATH}. Log a bet with `python log_bet.py new ...`.")

    st.caption("Click Rerun (top right, or press R) for the latest data.")

# ---------------------------------------------------------------------------
# Scanners tab
# ---------------------------------------------------------------------------
with tab_scanners:
    st.subheader("Basketball & soccer scanners")
    st.caption(
        "Starts/stops each scanner as a background process on this machine - no terminal needed. "
        "A scanner only runs while this PC is on and this dashboard's host process is alive."
    )

    for name, label, caveat in [
        ("soccer", "Soccer (EPL / UCL)", None),
        ("basketball", "Basketball (NBA)", "Untested against a live game - see README."),
    ]:
        st.markdown(f"#### {label}")
        st_status = scanner_manager.status(name)
        c1, c2, c3 = st.columns([1, 1, 3])
        with c1:
            if st_status["running"]:
                st.success(f"Running (pid {st_status['pid']})")
            else:
                st.error("Stopped")
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
        s_poll = st.number_input("Poll interval (seconds)", value=cfg["soccer"]["poll_interval_seconds"], min_value=15, key="s_poll")
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
        cfg["soccer"]["poll_interval_seconds"] = s_poll
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
    rows = [
        {"team": team, "player": player, "role": role}
        for team, players in key_players.items()
        for player, role in players.items()
    ]
    edited = st.data_editor(
        pd.DataFrame(rows, columns=["team", "player", "role"]),
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
