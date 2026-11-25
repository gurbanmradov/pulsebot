from __future__ import annotations

from typing import Sequence


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """Calculate Exponential Moving Average without external dependencies."""
    if period <= 0:
        raise ValueError("EMA period must be positive.")

    n = len(values)
    result: list[float | None] = [None] * n
    if n < period:
        return result

    prev = sum(values[:period]) / period
    result[period - 1] = prev
    k = 2.0 / (period + 1)

    for i in range(period, n):
        prev = (values[i] * k) + (prev * (1.0 - k))
        result[i] = prev
    return result


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_gain == 0.0 and avg_loss == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    """Calculate Relative Strength Index using Wilder's smoothing method."""
    if period <= 0:
        raise ValueError("RSI period must be positive.")

    n = len(values)
    result: list[float | None] = [None] * n
    if n <= period:
        return result

    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        if delta > 0:
            gains += delta
        else:
            losses += -delta

    avg_gain = gains / period
    avg_loss = losses / period
    result[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period + 1, n):
        delta = values[i] - values[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = -delta if delta < 0 else 0.0
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        result[i] = _rsi_from_averages(avg_gain, avg_loss)

    return result
