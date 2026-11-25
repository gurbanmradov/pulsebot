from __future__ import annotations

import datetime
import os
import sqlite3
from pathlib import Path
from typing import Any

from pulsebot.models import Trade


def default_ledger_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "pulsebot" / "ledger.db"


class TradeLedger:
    """SQLite-backed audit log for backtest and paper-trading runs."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_ledger_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mode TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                started_at TEXT NOT NULL,
                initial_balance REAL NOT NULL,
                final_balance REAL
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                price REAL NOT NULL,
                quantity REAL NOT NULL,
                fee REAL NOT NULL,
                timestamp INTEGER NOT NULL,
                reason TEXT NOT NULL,
                pnl REAL NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(id)
            );
            """
        )
        self._conn.commit()

    def start_run(self, *, mode: str, symbol: str, strategy: str, initial_balance: float) -> int:
        started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cur = self._conn.execute(
            """
            INSERT INTO runs (mode, symbol, strategy, started_at, initial_balance)
            VALUES (?, ?, ?, ?, ?)
            """,
            (mode, symbol.upper(), strategy, started_at, initial_balance),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, final_balance: float) -> None:
        self._conn.execute("UPDATE runs SET final_balance = ? WHERE id = ?", (final_balance, run_id))
        self._conn.commit()

    def record_trade(self, run_id: int, trade: Trade) -> None:
        self._conn.execute(
            """
            INSERT INTO trades (run_id, symbol, side, price, quantity, fee, timestamp, reason, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                trade.symbol,
                trade.side,
                trade.price,
                trade.quantity,
                trade.fee,
                trade.timestamp,
                trade.reason,
                trade.pnl,
            ),
        )
        self._conn.commit()

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_trades(self, limit: int = 20, run_id: int | None = None) -> list[dict[str, Any]]:
        if run_id is not None:
            rows = self._conn.execute(
                "SELECT * FROM trades WHERE run_id = ? ORDER BY id DESC LIMIT ?",
                (run_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> TradeLedger:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
