from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from pulsebot.models import Candle

DEFAULT_ENDPOINTS: tuple[str, ...] = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
)


class MarketDataError(RuntimeError):
    """Raised when market data cannot be fetched or parsed."""


class MarketDataClient(ABC):
    @abstractmethod
    def fetch_klines(self, symbol: str, interval: str = "1h", limit: int = 500) -> list[Candle]:
        raise NotImplementedError


def parse_binance_klines(raw_rows: list[list[Any]]) -> list[Candle]:
    """Convert raw Binance kline rows into Candle objects."""
    candles: list[Candle] = []
    for row in raw_rows:
        if len(row) < 6:
            continue
        candles.append(
            Candle(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
        )
    return candles


class BinancePublicClient(MarketDataClient):
    """Zero-dependency public Binance REST client with endpoint fallback and retries."""

    def __init__(
        self,
        endpoints: tuple[str, ...] = DEFAULT_ENDPOINTS,
        timeout: float = 10.0,
        retries: int = 2,
    ):
        self.endpoints = endpoints
        self.timeout = timeout
        self.retries = retries

    def _get_json(self, path: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode(params)
        last_error: Exception | None = None

        for base_url in self.endpoints:
            url = f"{base_url.rstrip('/')}{path}?{query}"
            for attempt in range(self.retries + 1):
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "pulsebot/0.1.0"})
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                    last_error = exc
                    if attempt < self.retries:
                        time.sleep(0.5 * (attempt + 1))

        raise MarketDataError(f"Failed to fetch market data from Binance: {last_error}")

    def fetch_klines(self, symbol: str, interval: str = "1h", limit: int = 500) -> list[Candle]:
        clamped_limit = max(1, min(1000, int(limit)))
        data = self._get_json(
            "/api/v3/klines",
            {"symbol": symbol.upper(), "interval": interval, "limit": clamped_limit},
        )
        if not isinstance(data, list):
            raise MarketDataError(f"Unexpected Binance response format: {type(data).__name__}")
        candles = parse_binance_klines(data)
        if not candles:
            raise MarketDataError(f"No candles returned for {symbol} ({interval}).")
        return candles
