import unittest

from pulsebot.indicators import ema, rsi
from pulsebot.models import Candle, Position, RiskConfig
from pulsebot.risk import RiskManager
from pulsebot.strategies import GridStrategy, RsiEmaDipStrategy


def _make_candles(closes: list[float], start_ts: int = 1_700_000_000_000) -> list[Candle]:
    return [
        Candle(
            timestamp=start_ts + i * 3_600_000,
            open=c,
            high=c * 1.005,
            low=c * 0.995,
            close=c,
            volume=100.0,
        )
        for i, c in enumerate(closes)
    ]


class TestIndicators(unittest.TestCase):
    def test_ema_basic_values(self):
        out = ema([10.0, 20.0, 30.0, 40.0], period=3)
        self.assertIsNone(out[0])
        self.assertIsNone(out[1])
        self.assertAlmostEqual(out[2], 20.0, places=5)
        self.assertAlmostEqual(out[3], 30.0, places=5)

    def test_rsi_extremes_and_flat(self):
        up = rsi([float(i) for i in range(1, 20)], period=14)
        down = rsi([float(30 - i) for i in range(20)], period=14)
        flat = rsi([50.0] * 20, period=14)

        self.assertAlmostEqual(up[-1], 100.0, places=4)
        self.assertAlmostEqual(down[-1], 0.0, places=4)
        self.assertAlmostEqual(flat[-1], 50.0, places=4)


class TestStrategies(unittest.TestCase):
    def test_rsi_ema_buys_dip_in_uptrend(self):
        closes = [100.0 + i for i in range(25)] + [122.5, 120.5, 118.5, 116.5]
        candles = _make_candles(closes)
        strategy = RsiEmaDipStrategy(rsi_period=5, ema_period=20, rsi_buy=35.0, trend_tolerance_pct=0.01)

        signal = strategy.evaluate(candles, position=None)
        self.assertEqual(signal.action, "BUY")

    def test_rsi_ema_blocks_falling_knife_downtrend(self):
        closes = [200.0 - 2.0 * i for i in range(30)]
        candles = _make_candles(closes)
        strategy = RsiEmaDipStrategy(rsi_period=5, ema_period=20, rsi_buy=35.0, trend_tolerance_pct=0.01)

        signal = strategy.evaluate(candles, position=None)
        self.assertEqual(signal.action, "HOLD")

    def test_grid_strategy_buy_and_sell(self):
        strategy = GridStrategy(step_pct=0.02, anchor_price=100.0)
        dip_signal = strategy.evaluate(_make_candles([97.5]), position=None)
        self.assertEqual(dip_signal.action, "BUY")

        pos = Position(symbol="BTCUSDT", quantity=1.0, entry_price=97.5, opened_at=1_700_000_000_000)
        rebound_signal = strategy.evaluate(_make_candles([100.0]), position=pos)
        self.assertEqual(rebound_signal.action, "SELL")


class TestRiskManager(unittest.TestCase):
    def test_allocation_cap_and_cooldown(self):
        rm = RiskManager(RiskConfig(max_position_pct=0.20, cooldown_candles=3))
        decision = rm.approve_buy(
            timestamp=1_700_000_000_000,
            candle_index=10,
            price=100.0,
            cash=1000.0,
            total_equity=1000.0,
            has_open_position=False,
        )
        self.assertTrue(decision.allowed)
        self.assertAlmostEqual(decision.quantity, 2.0, places=5)

        rm.record_exit(candle_index=10)
        blocked = rm.approve_buy(
            timestamp=1_700_000_000_000,
            candle_index=12,
            price=100.0,
            cash=1000.0,
            total_equity=1000.0,
            has_open_position=False,
        )
        self.assertFalse(blocked.allowed)

    def test_stop_loss_and_trailing_take_profit(self):
        rm = RiskManager(RiskConfig(stop_loss_pct=0.03, take_profit_pct=0.05, trailing_stop_pct=0.02))
        pos = Position(symbol="BTCUSDT", quantity=1.0, entry_price=100.0, opened_at=1_700_000_000_000)

        sl_signal = rm.check_position_exit(pos, _make_candles([96.5])[0])
        self.assertIsNotNone(sl_signal)
        self.assertEqual(sl_signal.action, "SELL")

        pos2 = Position(symbol="BTCUSDT", quantity=1.0, entry_price=100.0, opened_at=1_700_000_000_000)
        # Price rallies to 110 (arms trailing stop), should keep holding
        self.assertIsNone(rm.check_position_exit(pos2, _make_candles([110.0])[0]))
        # Pulls back more than 2% from 110 -> triggers trailing exit
        trail_signal = rm.check_position_exit(pos2, _make_candles([107.5])[0])
        self.assertIsNotNone(trail_signal)
        self.assertEqual(trail_signal.action, "SELL")

    def test_daily_kill_switch_halts_trading(self):
        rm = RiskManager(RiskConfig(daily_loss_limit_pct=0.03))
        ts = 1_700_000_000_000
        # Establish day starting equity at 1000
        rm.check_kill_switch(ts, 1000.0)
        # Drop equity to 965 (-3.5%)
        decision = rm.approve_buy(
            timestamp=ts + 60_000,
            candle_index=5,
            price=100.0,
            cash=965.0,
            total_equity=965.0,
            has_open_position=False,
        )
        self.assertFalse(decision.allowed)
        self.assertIn("Daily loss limit", decision.reason)


if __name__ == "__main__":
    unittest.main()
