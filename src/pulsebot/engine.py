from __future__ import annotations

import time
from typing import Callable, Sequence

from pulsebot.ledger import TradeLedger
from pulsebot.market import MarketDataClient, MarketDataError
from pulsebot.models import BacktestReport, Candle, Position, RiskConfig, Trade
from pulsebot.risk import RiskManager
from pulsebot.strategies import Strategy


class _PortfolioState:
    def __init__(self, initial_balance: float, fee_rate: float = 0.001, slippage_rate: float = 0.0005):
        self.initial_balance = initial_balance
        self.cash = initial_balance
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.position: Position | None = None
        self.entry_cost: float = 0.0
        self.total_fees: float = 0.0
        self.peak_equity: float = initial_balance
        self.max_drawdown_pct: float = 0.0
        self.trades: list[Trade] = []

    def equity(self, mark_price: float) -> float:
        pos_val = self.position.quantity * mark_price if self.position else 0.0
        return self.cash + pos_val

    def update_drawdown(self, mark_price: float) -> float:
        eq = self.equity(mark_price)
        if eq > self.peak_equity:
            self.peak_equity = eq
        if self.peak_equity > 0:
            dd = ((self.peak_equity - eq) / self.peak_equity) * 100.0
            if dd > self.max_drawdown_pct:
                self.max_drawdown_pct = dd
        return eq

    def execute_buy(self, symbol: str, candle: Candle, budget: float, reason: str) -> Trade:
        spend = min(self.cash, budget)
        exec_price = candle.close * (1.0 + self.slippage_rate)
        fee = spend * self.fee_rate
        net_spend = spend - fee
        qty = net_spend / exec_price

        self.cash -= spend
        self.total_fees += fee
        self.entry_cost = spend
        self.position = Position(
            symbol=symbol,
            quantity=qty,
            entry_price=exec_price,
            opened_at=candle.timestamp,
        )
        trade = Trade(
            symbol=symbol,
            side="BUY",
            price=exec_price,
            quantity=qty,
            fee=fee,
            timestamp=candle.timestamp,
            reason=reason,
            pnl=0.0,
        )
        self.trades.append(trade)
        return trade

    def execute_sell(self, candle: Candle, reason: str) -> Trade:
        if self.position is None:
            raise RuntimeError("Cannot sell without an open position.")

        pos = self.position
        exec_price = candle.close * (1.0 - self.slippage_rate)
        gross = pos.quantity * exec_price
        fee = gross * self.fee_rate
        net_proceeds = gross - fee
        pnl = net_proceeds - self.entry_cost

        self.cash += net_proceeds
        self.total_fees += fee
        self.position = None
        self.entry_cost = 0.0

        trade = Trade(
            symbol=pos.symbol,
            side="SELL",
            price=exec_price,
            quantity=pos.quantity,
            fee=fee,
            timestamp=candle.timestamp,
            reason=reason,
            pnl=pnl,
        )
        self.trades.append(trade)
        return trade


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        *,
        initial_balance: float = 1000.0,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        risk_config: RiskConfig | None = None,
        ledger: TradeLedger | None = None,
    ):
        self.strategy = strategy
        self.initial_balance = initial_balance
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.risk_manager = RiskManager(risk_config)
        self.ledger = ledger

    def run(self, symbol: str, candles: Sequence[Candle]) -> BacktestReport:
        if not candles:
            raise ValueError("Backtest requires at least one candle.")

        symbol = symbol.upper()
        portfolio = _PortfolioState(
            initial_balance=self.initial_balance,
            fee_rate=self.fee_rate,
            slippage_rate=self.slippage_rate,
        )
        run_id = (
            self.ledger.start_run(
                mode="backtest",
                symbol=symbol,
                strategy=self.strategy.name,
                initial_balance=self.initial_balance,
            )
            if self.ledger
            else None
        )

        halted_by_kill_switch = False

        for i, candle in enumerate(candles):
            eq = portfolio.update_drawdown(candle.close)
            halted, _ = self.risk_manager.check_kill_switch(candle.timestamp, eq)
            if halted:
                halted_by_kill_switch = True

            window = candles[: i + 1]

            if portfolio.position is not None:
                exit_sig = self.risk_manager.check_position_exit(portfolio.position, candle)
                if exit_sig is None:
                    strat_sig = self.strategy.evaluate(window, portfolio.position)
                    if strat_sig.action == "SELL":
                        exit_sig = strat_sig

                if exit_sig is not None:
                    trade = portfolio.execute_sell(candle, exit_sig.reason)
                    self.risk_manager.record_exit(i)
                    self.strategy.on_trade_closed(trade.price)
                    if self.ledger and run_id is not None:
                        self.ledger.record_trade(run_id, trade)
            else:
                strat_sig = self.strategy.evaluate(window, None)
                if strat_sig.action == "BUY":
                    decision = self.risk_manager.approve_buy(
                        timestamp=candle.timestamp,
                        candle_index=i,
                        price=candle.close,
                        cash=portfolio.cash,
                        total_equity=eq,
                        has_open_position=False,
                    )
                    if decision.allowed:
                        budget = min(portfolio.cash, decision.quantity * candle.close)
                        trade = portfolio.execute_buy(symbol, candle, budget, strat_sig.reason)
                        if self.ledger and run_id is not None:
                            self.ledger.record_trade(run_id, trade)

        final_eq = portfolio.equity(candles[-1].close)
        if self.ledger and run_id is not None:
            self.ledger.finish_run(run_id, final_eq)

        buy_hold_final = (
            self.initial_balance * (1.0 - self.fee_rate) * (candles[-1].close / candles[0].close)
            if candles[0].close > 0
            else self.initial_balance
        )

        return BacktestReport(
            symbol=symbol,
            strategy_name=self.strategy.name,
            initial_balance=self.initial_balance,
            final_balance=final_eq,
            buy_and_hold_final=buy_hold_final,
            total_fees=portfolio.total_fees,
            max_drawdown_pct=portfolio.max_drawdown_pct,
            trades=portfolio.trades,
            halted_by_kill_switch=halted_by_kill_switch,
        )


class PaperTrader:
    """Live paper-trading loop backed by real market prices and SQLite ledger."""

    def __init__(
        self,
        *,
        market: MarketDataClient,
        strategy: Strategy,
        symbol: str,
        interval: str = "1m",
        lookback: int = 100,
        initial_balance: float = 1000.0,
        fee_rate: float = 0.001,
        slippage_rate: float = 0.0005,
        risk_config: RiskConfig | None = None,
        ledger: TradeLedger | None = None,
    ):
        self.market = market
        self.strategy = strategy
        self.symbol = symbol.upper()
        self.interval = interval
        self.lookback = lookback
        self.portfolio = _PortfolioState(initial_balance, fee_rate, slippage_rate)
        self.risk_manager = RiskManager(risk_config)
        self.ledger = ledger
        self._step_index = 0
        self._last_candle_ts: int | None = None
        self.run_id = (
            self.ledger.start_run(
                mode="paper",
                symbol=self.symbol,
                strategy=self.strategy.name,
                initial_balance=initial_balance,
            )
            if self.ledger
            else None
        )

    def step(self) -> tuple[float, Trade | None, str]:
        candles = self.market.fetch_klines(self.symbol, self.interval, limit=self.lookback)
        latest = candles[-1]
        if self._last_candle_ts != latest.timestamp:
            self._step_index += 1
            self._last_candle_ts = latest.timestamp

        eq = self.portfolio.update_drawdown(latest.close)
        self.risk_manager.check_kill_switch(latest.timestamp, eq)

        if self.portfolio.position is not None:
            exit_sig = self.risk_manager.check_position_exit(self.portfolio.position, latest)
            if exit_sig is None:
                strat_sig = self.strategy.evaluate(candles, self.portfolio.position)
                if strat_sig.action == "SELL":
                    exit_sig = strat_sig
            if exit_sig is not None:
                trade = self.portfolio.execute_sell(latest, exit_sig.reason)
                self.risk_manager.record_exit(self._step_index)
                self.strategy.on_trade_closed(trade.price)
                if self.ledger and self.run_id is not None:
                    self.ledger.record_trade(self.run_id, trade)
                return self.portfolio.equity(latest.close), trade, f"SELL: {exit_sig.reason}"
            return eq, None, f"HOLD position (price={latest.close:.2f})"

        strat_sig = self.strategy.evaluate(candles, None)
        if strat_sig.action == "BUY":
            decision = self.risk_manager.approve_buy(
                timestamp=latest.timestamp,
                candle_index=self._step_index,
                price=latest.close,
                cash=self.portfolio.cash,
                total_equity=eq,
                has_open_position=False,
            )
            if decision.allowed:
                budget = min(self.portfolio.cash, decision.quantity * latest.close)
                trade = self.portfolio.execute_buy(self.symbol, latest, budget, strat_sig.reason)
                if self.ledger and self.run_id is not None:
                    self.ledger.record_trade(self.run_id, trade)
                return self.portfolio.equity(latest.close), trade, f"BUY: {strat_sig.reason}"
            return eq, None, f"BUY blocked by RiskManager: {decision.reason}"

        return eq, None, f"HOLD ({strat_sig.reason})"

    def run(
        self,
        *,
        poll_seconds: float = 30.0,
        max_cycles: int = 0,
        on_tick: Callable[[int, float, Trade | None, str], None] | None = None,
    ) -> float:
        cycle = 0
        last_equity = self.portfolio.cash
        try:
            while max_cycles <= 0 or cycle < max_cycles:
                cycle += 1
                try:
                    last_equity, trade, note = self.step()
                    if on_tick:
                        on_tick(cycle, last_equity, trade, note)
                except MarketDataError as exc:
                    if on_tick:
                        on_tick(cycle, last_equity, None, f"Network warning: {exc}")
                if max_cycles <= 0 or cycle < max_cycles:
                    time.sleep(poll_seconds)
        except KeyboardInterrupt:
            pass
        finally:
            if self.ledger and self.run_id is not None:
                self.ledger.finish_run(self.run_id, last_equity)
        return last_equity
