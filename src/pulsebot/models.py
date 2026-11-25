from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Action = Literal["BUY", "SELL", "HOLD"]
Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Candle:
    timestamp: int  # Unix timestamp in milliseconds
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Signal:
    action: Action
    reason: str = ""


@dataclass
class Position:
    symbol: str
    quantity: float
    entry_price: float
    opened_at: int
    highest_price: float = 0.0

    def __post_init__(self) -> None:
        if self.highest_price <= 0.0:
            self.highest_price = self.entry_price


@dataclass(frozen=True)
class Trade:
    symbol: str
    side: Side
    price: float
    quantity: float
    fee: float
    timestamp: int
    reason: str
    pnl: float = 0.0


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float = 0.25      # Max fraction of equity allocated per trade
    stop_loss_pct: float = 0.03         # Hard stop-loss below entry (3%)
    take_profit_pct: float = 0.05       # Profit level that arms trailing stop (5%)
    trailing_stop_pct: float = 0.02     # Exit if price pulls back 2% from peak
    daily_loss_limit_pct: float = 0.03  # Kill-switch threshold per UTC day (3%)
    cooldown_candles: int = 3           # Minimum candles to wait after closing a trade


@dataclass
class BacktestReport:
    symbol: str
    strategy_name: str
    initial_balance: float
    final_balance: float
    buy_and_hold_final: float
    total_fees: float
    max_drawdown_pct: float
    trades: list[Trade] = field(default_factory=list)
    halted_by_kill_switch: bool = False

    @property
    def return_pct(self) -> float:
        if self.initial_balance <= 0:
            return 0.0
        return ((self.final_balance - self.initial_balance) / self.initial_balance) * 100.0

    @property
    def buy_and_hold_pct(self) -> float:
        if self.initial_balance <= 0:
            return 0.0
        return ((self.buy_and_hold_final - self.initial_balance) / self.initial_balance) * 100.0

    @property
    def win_rate_pct(self) -> float:
        closed = [t for t in self.trades if t.side == "SELL"]
        if not closed:
            return 0.0
        wins = sum(1 for t in closed if t.pnl > 0)
        return (wins / len(closed)) * 100.0
