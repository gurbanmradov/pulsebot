import tempfile
import unittest
from pathlib import Path

from pulsebot.cli import main as cli_main
from pulsebot.engine import BacktestEngine, PaperTrader
from pulsebot.ledger import TradeLedger
from pulsebot.market import MarketDataClient, parse_binance_klines
from pulsebot.models import Candle, RiskConfig
from pulsebot.strategies import GridStrategy


def _candles_from_prices(prices: list[float], start_ts: int = 1_700_000_000_000, step_ms: int = 3_600_000) -> list[Candle]:
    return [
        Candle(
            timestamp=start_ts + i * step_ms,
            open=p,
            high=p * 1.005,
            low=p * 0.995,
            close=p,
            volume=50.0,
        )
        for i, p in enumerate(prices)
    ]


class FakeMarketClient(MarketDataClient):
    def __init__(self, batches: list[list[Candle]]):
        self._batches = list(batches)
        self._index = 0

    def fetch_klines(self, symbol: str, interval: str = "1m", limit: int = 100) -> list[Candle]:
        batch = self._batches[min(self._index, len(self._batches) - 1)]
        self._index += 1
        return batch


class TestEngineAndLedger(unittest.TestCase):
    def test_parse_binance_klines(self):
        raw = [
            [1700000000000, "35000.0", "35500.0", "34900.0", "35200.0", "120.5", 1700003599999],
            [1700003600000, "35200.0", "35600.0", "35100.0", "35400.0", "98.2", 1700007199999],
        ]
        candles = parse_binance_klines(raw)
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0].close, 35200.0)
        self.assertEqual(candles[1].volume, 98.2)

    def test_backtest_executes_grid_cycle_and_persists_to_sqlite(self):
        # Anchor at 100 -> dip to 97 (BUY) -> rebound to 101 (SELL)
        candles = _candles_from_prices([100.0, 99.0, 97.0, 98.5, 101.0, 101.5])
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"
            with TradeLedger(db_path) as ledger:
                engine = BacktestEngine(
                    GridStrategy(step_pct=0.02),
                    initial_balance=1000.0,
                    fee_rate=0.001,
                    slippage_rate=0.0,
                    risk_config=RiskConfig(max_position_pct=0.5, cooldown_candles=1),
                    ledger=ledger,
                )
                report = engine.run("BTCUSDT", candles)

                self.assertEqual(len(report.trades), 2)
                self.assertEqual(report.trades[0].side, "BUY")
                self.assertEqual(report.trades[1].side, "SELL")
                self.assertGreater(report.final_balance, 1000.0)
                self.assertGreater(report.total_fees, 0.0)
                self.assertEqual(report.win_rate_pct, 100.0)

                runs = ledger.recent_runs()
                trades = ledger.recent_trades()
                self.assertEqual(len(runs), 1)
                self.assertEqual(len(trades), 2)

            # Verify CLI history command reads the saved SQLite DB cleanly
            self.assertEqual(cli_main(["history", "--db", str(db_path)]), 0)

    def test_backtest_kill_switch_triggers_on_sharp_drop(self):
        # Single UTC day: anchor 100 -> buy at 97 -> crash to 85 (hits stop-loss & daily loss limit)
        candles = _candles_from_prices(
            [100.0, 97.0, 85.0, 82.0, 80.0],
            start_ts=1_700_006_400_000,
            step_ms=60_000,
        )
        engine = BacktestEngine(
            GridStrategy(step_pct=0.02),
            initial_balance=1000.0,
            risk_config=RiskConfig(max_position_pct=0.5, stop_loss_pct=0.08, daily_loss_limit_pct=0.03),
        )
        report = engine.run("ETHUSDT", candles)
        self.assertTrue(report.halted_by_kill_switch)

    def test_paper_trader_step_lifecycle(self):
        b1 = _candles_from_prices([100.0])
        b2 = _candles_from_prices([100.0, 97.5])
        b3 = _candles_from_prices([100.0, 97.5, 101.0])
        market = FakeMarketClient([b1, b2, b3])

        with tempfile.TemporaryDirectory() as tmp:
            with TradeLedger(Path(tmp) / "paper.db") as ledger:
                trader = PaperTrader(
                    market=market,
                    strategy=GridStrategy(step_pct=0.02),
                    symbol="SOLUSDT",
                    initial_balance=1000.0,
                    slippage_rate=0.0,
                    ledger=ledger,
                )
                _, t1, _ = trader.step()
                _, t2, _ = trader.step()
                eq3, t3, _ = trader.step()

                self.assertIsNone(t1)
                self.assertIsNotNone(t2)
                self.assertEqual(t2.side, "BUY")
                self.assertIsNotNone(t3)
                self.assertEqual(t3.side, "SELL")
                self.assertGreater(eq3, 1000.0)
                self.assertEqual(len(ledger.recent_trades()), 2)


if __name__ == "__main__":
    unittest.main()
