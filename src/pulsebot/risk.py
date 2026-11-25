from __future__ import annotations

import datetime
from dataclasses import dataclass

from pulsebot.models import Candle, Position, RiskConfig, Signal


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str
    quantity: float = 0.0


def _utc_day(timestamp: int) -> str:
    ts_seconds = timestamp / 1000.0 if timestamp > 10_000_000_000 else float(timestamp)
    return datetime.datetime.fromtimestamp(ts_seconds, tz=datetime.timezone.utc).strftime("%Y-%m-%d")


class RiskManager:
    """Enforces position sizing, stop-loss, trailing profit, cooldown, and daily kill-switch."""

    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()
        self._daily_start_equity: dict[str, float] = {}
        self._halted_days: set[str] = set()
        self._last_exit_index: int | None = None

    def check_kill_switch(self, timestamp: int, current_equity: float) -> tuple[bool, str]:
        day = _utc_day(timestamp)
        if day not in self._daily_start_equity:
            self._daily_start_equity[day] = current_equity

        if day in self._halted_days:
            return True, f"Kill-switch active for {day}"

        start_eq = self._daily_start_equity[day]
        if start_eq > 0:
            drawdown = (start_eq - current_equity) / start_eq
            if drawdown >= self.config.daily_loss_limit_pct:
                self._halted_days.add(day)
                return True, f"Daily loss limit hit ({drawdown * 100:.2f}% on {day})"

        return False, ""

    def approve_buy(
        self,
        *,
        timestamp: int,
        candle_index: int,
        price: float,
        cash: float,
        total_equity: float,
        has_open_position: bool,
    ) -> RiskDecision:
        if has_open_position:
            return RiskDecision(False, "Position already open")

        halted, halt_reason = self.check_kill_switch(timestamp, total_equity)
        if halted:
            return RiskDecision(False, halt_reason)

        if self._last_exit_index is not None:
            elapsed = candle_index - self._last_exit_index
            if elapsed < self.config.cooldown_candles:
                return RiskDecision(False, f"Cooldown active ({elapsed}/{self.config.cooldown_candles} candles)")

        if price <= 0 or cash <= 0:
            return RiskDecision(False, "Invalid price or zero cash")

        budget = min(cash, total_equity * self.config.max_position_pct)
        if budget <= 0:
            return RiskDecision(False, "Allocated budget is zero")

        return RiskDecision(True, "Approved", quantity=budget / price)

    def check_position_exit(self, position: Position, candle: Candle) -> Signal | None:
        price = candle.close
        if price > position.highest_price:
            position.highest_price = price

        stop_price = position.entry_price * (1.0 - self.config.stop_loss_pct)
        if price <= stop_price:
            return Signal("SELL", f"Stop-loss triggered ({price:.2f} <= {stop_price:.2f})")

        arm_price = position.entry_price * (1.0 + self.config.take_profit_pct)
        if position.highest_price >= arm_price:
            trailing_stop = position.highest_price * (1.0 - self.config.trailing_stop_pct)
            if price <= trailing_stop:
                return Signal("SELL", f"Trailing take-profit triggered ({price:.2f} <= {trailing_stop:.2f})")

        return None

    def record_exit(self, candle_index: int) -> None:
        self._last_exit_index = candle_index
