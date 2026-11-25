# pulsebot

[![CI](https://github.com/gurbanmradov/pulsebot/actions/workflows/ci.yml/badge.svg)](https://github.com/gurbanmradov/pulsebot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

**Zero-dependency algorithmic crypto backtesting and paper-trading engine in Python with a mandatory risk shield, SQLite trade ledger, and Buy & Hold benchmarking.**

`pulsebot` runs the exact same strategy and risk engine across historical backtests and live paper-trading sessions so ideas can be validated with exchange fees, slippage, and drawdown metrics before risking real capital.

---

## Features

- **Unified Backtest & Paper-Trading Engine:** Uses identical portfolio accounting and risk enforcement across historical simulations and live Binance price feeds (no API key required).
- **Mandatory Risk Guardrails (`RiskManager`):** Enforces per-trade allocation caps, hard stop-loss, trailing take-profit, post-trade cooldowns, and a daily drawdown kill-switch.
- **Configurable Strategies (Stdlib Only):** Includes a trend-filtered dip strategy (`rsi_ema_dip`) and a rebound `grid` strategy with from-scratch Wilder RSI and EMA implementations.
- **SQLite Trade Ledger:** Persists every run and executed trade locally (`runs` and `trades` tables) for repeatable audits.
- **Buy & Hold Benchmark:** Compares net strategy return (after fees and slippage) directly against passive Buy & Hold over the exact same window.

---

## Installation

```bash
git clone [https://github.com/gurbanmradov/pulsebot.git](https://github.com/gurbanmradov/pulsebot.git)
cd pulsebot
pip install .
```

---

## Usage

Run a historical backtest on the last 500 hourly BTC/USDT candles:
```bash
pulsebot backtest --symbol BTCUSDT --interval 1h --limit 500 --strategy rsi_ema_dip --balance 1000
```

Tune `rsi_ema_dip` parameters for daily (`1d`) candles:
```bash
pulsebot backtest --symbol BTCUSDT --interval 1d --limit 365 \
  --strategy rsi_ema_dip --rsi-buy 42 --ema-period 50 --trend-tolerance 0.08
```

Test the Grid strategy on SOL/USDT:
```bash
pulsebot backtest --symbol SOLUSDT --interval 1h --limit 500 --strategy grid --grid-step 0.02
```

Start a live virtual paper-trading session (Ctrl+C to stop):
```bash
pulsebot paper --symbol BTCUSDT --interval 1m --strategy grid --poll 30
```

Inspect recent runs recorded in SQLite:
```bash
pulsebot history
```

---

## Sample Backtest Benchmark (Bearish 500h Window)

Measured on 500 consecutive `1h` Binance candles starting from `$1,000.00` (0.1% fee, 0.05% slippage):

| Pair & Strategy | Final Balance | Buy & Hold | Alpha vs B&H | Max Drawdown | Fees Paid |
|---|---:|---:|---:|---:|---:|
| `BTCUSDT` — `rsi_ema_dip` | $990.14 (-0.99%) | $742.39 (-25.76%) | +24.77% | 2.07% | $0.74 |
| `BTCUSDT` — `grid` (2%) | $955.18 (-4.48%) | $742.30 (-25.77%) | +21.29% | 5.56% | $4.32 |
| `SOLUSDT` — `grid` (2%) | $957.28 (-4.27%) | $731.18 (-26.88%) | +22.61% | 4.59% | $7.81 |

---

## Development & Testing

```bash
python -m unittest discover -s tests -v
```

> **Disclaimer:** `pulsebot` is built for software engineering education, strategy research, and paper trading. Algorithmic trading carries significant financial risk.
