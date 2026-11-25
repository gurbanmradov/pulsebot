import unittest

from pulsebot.cli import build_parser, build_strategy
from pulsebot.strategies import GridStrategy, RsiEmaDipStrategy


class TestCliStrategyOptions(unittest.TestCase):
    def test_backtest_parses_custom_rsi_ema_flags(self):
        parser = build_parser()
        args = parser.parse_args(
            [
                "backtest",
                "--symbol",
                "BTCUSDT",
                "--interval",
                "1d",
                "--strategy",
                "rsi_ema_dip",
                "--rsi-buy",
                "42",
                "--rsi-sell",
                "70",
                "--ema-period",
                "30",
                "--trend-tolerance",
                "0.08",
            ]
        )
        strategy = build_strategy(
            args.strategy,
            grid_step=args.grid_step,
            rsi_buy=args.rsi_buy,
            rsi_sell=args.rsi_sell,
            ema_period=args.ema_period,
            trend_tolerance=args.trend_tolerance,
        )
        self.assertIsInstance(strategy, RsiEmaDipStrategy)
        self.assertEqual(strategy.rsi_buy, 42.0)
        self.assertEqual(strategy.rsi_sell, 70.0)
        self.assertEqual(strategy.ema_period, 30)
        self.assertEqual(strategy.trend_tolerance_pct, 0.08)

    def test_paper_parses_grid_step(self):
        parser = build_parser()
        args = parser.parse_args(["paper", "--strategy", "grid", "--grid-step", "0.015"])
        strategy = build_strategy(args.strategy, grid_step=args.grid_step)
        self.assertIsInstance(strategy, GridStrategy)
        self.assertEqual(strategy.step_pct, 0.015)


if __name__ == "__main__":
    unittest.main()
