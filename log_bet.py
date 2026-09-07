"""
log_bet.py

Command-line wrapper around clv_engine.BetLogger so logging a bet, its
closing line, and its outcome doesn't require writing Python each time.
Uses the same BETS_DB_PATH env var as dashboard.py (default "bets.db"),
so anything logged here shows up in the dashboard's CLV panel.

Usage:
    # after you act on a Telegram alert and place a bet - pass
    # --trigger-rule (the rule_name from the alert / triggers.log.jsonl)
    # so the dashboard's Analytics tab can tell you which rules are
    # actually worth acting on:
    python log_bet.py new --sport NBA --game-id 401584669 \\
        --market "Q1 total" --selection "Over 54.5" --stake 1 --odds 1.95 \\
        --trigger-rule bonus_trigger

    # near the game's close, snapshot the full closing market for that
    # side of the bet (all outcomes, needed to de-vig properly):
    python log_bet.py close <bet_id> --closing-odds 1.87,2.02 --index 0

    # once the game finishes:
    python log_bet.py outcome <bet_id> win

    # anytime:
    python log_bet.py summary
    python log_bet.py summary --by-rule
"""

from __future__ import annotations

import argparse
import os

from dotenv import load_dotenv

from clv_engine import BetLogger

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Log bets and track CLV against triggers.log.jsonl alerts.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_new = sub.add_parser("new", help="Log a new bet you placed off an alert.")
    p_new.add_argument("--sport", required=True, help='e.g. "NBA", "NCAAM", "EPL", "UCL"')
    p_new.add_argument("--game-id", required=True, help="game_id from the Telegram alert / triggers.log.jsonl")
    p_new.add_argument("--market", required=True, help='e.g. "total", "spread", "moneyline", "corners_over_9.5"')
    p_new.add_argument("--selection", required=True, help='e.g. "PHI -3.5", "Over 54.5"')
    p_new.add_argument("--stake", type=float, required=True, help="stake in units")
    p_new.add_argument("--odds", type=float, required=True, help="decimal odds you took")
    p_new.add_argument("--trigger-rule", default=None, help="rule_name from the alert, e.g. bonus_trigger - enables per-rule CLV breakdown")

    p_close = sub.add_parser("close", help="Record the closing line for a bet and compute its CLV.")
    p_close.add_argument("bet_id")
    p_close.add_argument("--closing-odds", required=True, help="comma-separated decimal odds for every outcome, e.g. 1.87,2.02")
    p_close.add_argument("--index", type=int, required=True, help="index into --closing-odds for the side you bet")
    p_close.add_argument("--method", choices=["power", "multiplicative"], default="power")

    p_outcome = sub.add_parser("outcome", help="Record whether a bet won, lost, or pushed.")
    p_outcome.add_argument("bet_id")
    p_outcome.add_argument("result", choices=["win", "loss", "push"])

    p_summary = sub.add_parser("summary", help="Print overall bet count, win rate, avg CLV, and net units.")
    p_summary.add_argument("--by-rule", action="store_true", help="break the summary down by trigger_rule instead of one overall total")

    args = parser.parse_args()
    logger = BetLogger(db_path=os.environ.get("BETS_DB_PATH", "bets.db"))

    if args.command == "new":
        bet_id = logger.log_bet(
            sport=args.sport,
            game_id=args.game_id,
            market=args.market,
            selection=args.selection,
            stake=args.stake,
            odds_taken=args.odds,
            trigger_rule=args.trigger_rule,
        )
        print(f"Logged bet {bet_id}")

    elif args.command == "close":
        closing_odds = [float(x) for x in args.closing_odds.split(",")]
        clv = logger.record_closing_line(args.bet_id, closing_odds, args.index, method=args.method)
        sign = "+" if clv >= 0 else ""
        print(f"CLV: {sign}{clv:.2f}% ({'beat' if clv >= 0 else 'lost to'} the closing line)")

    elif args.command == "outcome":
        logger.record_outcome(args.bet_id, args.result)
        print(f"Recorded {args.bet_id} as {args.result}")

    elif args.command == "summary":
        if args.by_rule:
            for rule, s in logger.summary_by_rule().items():
                print(f"=== {rule} ===")
                _print_summary(s)
        else:
            _print_summary(logger.summary())


def _print_summary(s: dict) -> None:
    print(f"Total bets:    {s['total_bets']}")
    print(f"Settled bets:  {s['settled_bets']}")
    print(f"Win rate:      {s['win_rate']:.1%}" if s["win_rate"] is not None else "Win rate:      n/a")
    print(f"Avg CLV:       {s['avg_clv_pct']:.2f}%" if s["avg_clv_pct"] is not None else "Avg CLV:       n/a")
    print(f"Net units:     {s['net_units']:.2f}")


if __name__ == "__main__":
    main()
