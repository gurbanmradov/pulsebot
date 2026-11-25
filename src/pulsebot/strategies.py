from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from pulsebot.indicators import ema, rsi
from pulsebot.models import Candle, Position, Signal


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def evaluate(self, candles: Sequence[Candle], position: Position | None) -> Signal:
        raise NotImplementedError

    def on_trade_closed(self, exit_price: float) -> None:
        """Optional hook called by the engine after a position closes."""


class RsiEmaDipStrategy(Strategy):
    """Buys oversold dips only when price holds near or above the EMA trend filter."""

    name = "rsi_ema_dip"

    def __init__(
        self,
        rsi_period: int = 14,
        ema_period: int = 50,
        rsi_buy: float = 35.0,
        rsi_sell: float = 68.0,
        trend_tolerance_pct: float = 0.02,
    ):
        self.rsi_period = rsi_period
        self.ema_period = ema_period
        self.rsi_buy = rsi_buy
        self.rsi_sell = rsi_sell
        self.trend_tolerance_pct = trend_tolerance_pct

    def evaluate(self, candles: Sequence[Candle], position: Position | None) -> Signal:
        min_needed = max(self.rsi_period + 1, self.ema_period)
        if len(candles) < min_needed:
            return Signal("HOLD", "warming up indicators")

        closes = [c.close for c in candles]
        current_close = closes[-1]
        current_rsi = rsi(closes, self.rsi_period)[-1]
        current_ema = ema(closes, self.ema_period)[-1]

        if current_rsi is None or current_ema is None:
            return Signal("HOLD", "indicators not ready")

        if position is None:
            trend_floor = current_ema * (1.0 - self.trend_tolerance_pct)
            if current_rsi <= self.rsi_buy and current_close >= trend_floor:
                return Signal("BUY", f"RSI dip ({current_rsi:.1f}) above EMA trend ({current_ema:.2f})")
            return Signal("HOLD", "no entry setup")

        if current_rsi >= self.rsi_sell:
            return Signal("SELL", f"RSI overbought ({current_rsi:.1f})")
        return Signal("HOLD", "holding position")


class GridStrategy(Strategy):
    """Buys fixed percentage dips below an anchor price and sells on rebounds."""

    name = "grid"

    def __init__(self, step_pct: float = 0.02, anchor_price: float | None = None):
        if step_pct <= 0:
            raise ValueError("Grid step_pct must be positive.")
        self.step_pct = step_pct
        self._anchor = anchor_price

    def evaluate(self, candles: Sequence[Candle], position: Position | None) -> Signal:
        if not candles:
            return Signal("HOLD", "no market data")

        close = candles[-1].close
        if self._anchor is None:
            self._anchor = close
            return Signal("HOLD", "initialized grid anchor")

        if position is None:
            buy_level = self._anchor * (1.0 - self.step_pct)
            if close <= buy_level:
                return Signal("BUY", f"Grid buy level reached ({close:.2f} <= {buy_level:.2f})")
            if close >= self._anchor * (1.0 + self.step_pct):
                # Re-center anchor upward in a breakout so the grid doesn't stall
                self._anchor = close
            return Signal("HOLD", "inside neutral grid band")

        sell_level = position.entry_price * (1.0 + self.step_pct)
        if close >= sell_level:
            return Signal("SELL", f"Grid profit target reached ({close:.2f} >= {sell_level:.2f})")
        return Signal("HOLD", "waiting for grid rebound")

    def on_trade_closed(self, exit_price: float) -> None:
        self._anchor = exit_price
