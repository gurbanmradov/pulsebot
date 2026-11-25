from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from pulsebot import __version__
from pulsebot.engine import BacktestEngine, PaperTrader
from pulsebot.ledger import TradeLedger
from pulsebot.market import BinancePublicClient, MarketDataError
from pulsebot.models import BacktestReport, RiskConfig, Trade
from pulsebot.strategies import GridStrategy, RsiEmaDipStrategy, Strategy


def build_strategy(
    name: str,
    *,
    grid_step: float = 0.02,
    rsi_buy: float = 35.0,
    rsi_sell: float = 68.0,
    ema_period: int = 50,
    trend_tolerance: float = 0.02,
) -> Strategy:
    if name == "rsi_ema_dip":
        return RsiEmaDipStrategy(
            ema_period=ema_period,
            rsi_buy=rsi_buy,
            rsi_sell=rsi_sell,
            trend_tolerance_pct=trend_tolerance,
        )
    if name == "grid":
        return GridStrategy(step_pct=grid_step)
    raise ValueError(f"Unknown strategy: {name}")


def _add_strategy_args(subparser: argparse.ArgumentParser, default_strategy: str, default_grid_step: float) -> None:
    subparser.add_argument("--strategy", choices=["rsi_ema_dip", "grid"], default=default_strategy)
    subparser.add_argument(
        "--grid-step",
        type=float,
        default=default_grid_step,
        help=f"Grid step fraction (default: {default_grid_step})",
    )
    subparser.add_argument("--rsi-buy", type=float, default=35.0, help="RSI entry threshold (default: 35.0)")
    subparser.add_argument("--rsi-sell", type=float, default=68.0, help="RSI exit threshold (default: 68.0)")
    subparser.add_argument("--ema-period", type=int, default=50, help="EMA trend filter period (default: 50)")
    subparser.add_argument(
        "--trend-tolerance",
        type=float,
        default=0.02,
        help="Max fraction below EMA allowed for dip entry (default: 0.02 = 2%%)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pulsebot",
        description="Algorithmic crypto backtesting and paper-trading engine with risk guardrails.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    bt = subparsers.add_parser("backtest", help="Run historical backtest on Binance public market data.")
    bt.add_argument("--symbol", default="BTCUSDT", help="Trading pair (default: BTCUSDT)")
    bt.add_argument("--interval", default="1h", help="Candle interval e.g. 15m, 1h, 4h, 1d (default: 1h)")
    bt.add_argument("--limit", type=int, default=500, help="Number of historical candles, max 1000 (default: 500)")
    _add_strategy_args(bt, default_strategy="rsi_ema_dip", default_grid_step=0.02)
    bt.add_argument("--balance", type=float, default=1000.0, help="Starting USDT balance (default: 1000)")
    bt.add_argument("--fee", type=float, default=0.001, help="Exchange fee fraction (default: 0.001 = 0.1%%)")
    bt.add_argument("--db", type=Path, default=None, help="Optional SQLite ledger path to save results")

    paper = subparsers.add_parser("paper", help="Run live paper-trading loop against real Binance prices.")
    paper.add_argument("--symbol", default="BTCUSDT", help="Trading pair (default: BTCUSDT)")
    paper.add_argument("--interval", default="1m", help="Candle interval (default: 1m)")
    _add_strategy_args(paper, default_strategy="grid", default_grid_step=0.01)
    paper.add_argument("--balance", type=float, default=1000.0, help="Starting virtual USDT balance")
    paper.add_argument("--poll", type=float, default=30.0, help="Seconds between market checks (default: 30)")
    paper.add_argument("--cycles", type=int, default=0, help="Number of cycles (0 = run until Ctrl+C)")
    paper.add_argument("--db", type=Path, default=None, help="SQLite ledger path")

    hist = subparsers.add_parser("history", help="Inspect recent runs and trades recorded in SQLite.")
    hist.add_argument("--db", type=Path, default=None, help="SQLite ledger path")
    hist.add_argument("--limit", type=int, default=10, help="Max rows to display (default: 10)")

    return parser


def _print_backtest_report(report: BacktestReport, candle_count: int, interval: str) -> None:
    closed_count = sum(1 for t in report.trades if t.side == "SELL")
    alpha = report.return_pct - report.buy_and_hold_pct
    print(f"=== PulseBot Backtest: {report.symbol} ({interval}, {candle_count} candles) ===")
    print(f"Strategy          : {report.strategy_name}")
    print(f"Initial Balance   : ${report.initial_balance:,.2f}")
    print(f"Final Balance     : ${report.final_balance:,.2f} ({report.return_pct:+.2f}%)")
    print(f"Buy & Hold Final  : ${report.buy_and_hold_final:,.2f} ({report.buy_and_hold_pct:+.2f}%)")
    print(f"Alpha vs B&H      : {alpha:+.2f}%")
    print(f"Total Fees Paid   : ${report.total_fees:,.2f}")
    print(f"Max Drawdown      : {report.max_drawdown_pct:.2f}%")
    print(f"Trades Executed   : {len(report.trades)} ({closed_count} closed | Win Rate: {report.win_rate_pct:.1f}%)")
    if report.halted_by_kill_switch:
        print("Risk Guardrail    : Daily kill-switch triggered during test.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "backtest":
            client = BinancePublicClient()
            candles = client.fetch_klines(args.symbol, args.interval, args.limit)
            strategy = build_strategy(
                args.strategy,
                grid_step=args.grid_step,
                rsi_buy=args.rsi_buy,
                rsi_sell=args.rsi_sell,
                ema_period=args.ema_period,
                trend_tolerance=args.trend_tolerance,
            )

            with TradeLedger(args.db) as ledger:
                engine = BacktestEngine(
                    strategy,
                    initial_balance=args.balance,
                    fee_rate=args.fee,
                    risk_config=RiskConfig(),
                    ledger=ledger,
                )
                report = engine.run(args.symbol, candles)
                _print_backtest_report(report, len(candles), args.interval)
                print(f"Ledger saved to   : {ledger.path}")
            return 0

        if args.command == "paper":
            client = BinancePublicClient()
            strategy = build_strategy(
                args.strategy,
                grid_step=args.grid_step,
                rsi_buy=args.rsi_buy,
                rsi_sell=args.rsi_sell,
                ema_period=args.ema_period,
                trend_tolerance=args.trend_tolerance,
            )
            print(f"Starting paper trader for {args.symbol.upper()} ({args.strategy}) — Ctrl+C to stop.")

            def _log_tick(cycle: int, equity: float, trade: Trade | None, note: str) -> None:
                trade_tag = f" | {trade.side} {trade.quantity:.6f} @ ${trade.price:,.2f}" if trade else ""
                print(f"[#{cycle}] Equity: ${equity:,.2f}{trade_tag} — {note}")

            with TradeLedger(args.db) as ledger:
                trader = PaperTrader(
                    market=client,
                    strategy=strategy,
                    symbol=args.symbol,
                    interval=args.interval,
                    initial_balance=args.balance,
                    ledger=ledger,
                )
                final_eq = trader.run(poll_seconds=args.poll, max_cycles=args.cycles, on_tick=_log_tick)
                print(f"Paper session stopped. Final virtual equity: ${final_eq:,.2f} (ledger: {ledger.path})")
            return 0

        if args.command == "history":
            with TradeLedger(args.db) as ledger:
                runs = ledger.recent_runs(limit=args.limit)
                if not runs:
                    print(f"No runs recorded yet in {ledger.path}.")
                    return 0
                print(f"Recent runs in {ledger.path}:")
                for r in runs:
                    fin = f"${r['final_balance']:,.2f}" if r["final_balance"] is not None else "running"
                    print(
                        f"  #{r['id']} [{r['mode']}] {r['symbol']} ({r['strategy']}) "
                        f"${r['initial_balance']:,.2f} -> {fin}"
                    )
            return 0

    except (MarketDataError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
