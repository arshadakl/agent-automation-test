"""Paper trading simulator backed by SQLite."""

from __future__ import annotations

import sqlite3
from datetime import datetime, date
from typing import Any

from loguru import logger

from strategies.base_strategy import Signal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    symbol TEXT,
    direction TEXT,
    strategy TEXT,
    entry_price REAL,
    exit_price REAL,
    quantity INTEGER,
    stop_loss REAL,
    target REAL,
    pnl REAL,
    pnl_percent REAL,
    exit_reason TEXT,
    holding_time_minutes INTEGER,
    status TEXT
);

CREATE TABLE IF NOT EXISTS paper_daily_summary (
    date TEXT PRIMARY KEY,
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    gross_pnl REAL,
    charges_estimate REAL,
    net_pnl REAL,
    max_drawdown REAL,
    capital_used REAL
);
"""

# Rough intraday charge per trade (buy + sell round trip ~ 0.05% of turnover)
_CHARGE_PCT = 0.0005


class PaperTrader:
    """Simulates order execution using LTP and persists results to SQLite."""

    def __init__(self, db_path: str = "data/paper_trades.db", initial_capital: float = 100000.0) -> None:
        self._db_path = db_path
        self._initial_capital = initial_capital
        self._capital = initial_capital
        self._conn: sqlite3.Connection | None = None
        self._open_trades: dict[int, dict[str, Any]] = {}  # id → trade record
        self._next_id = 1

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the SQLite connection and create tables."""
        import os
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True) if os.path.dirname(self._db_path) else None
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # Restore next_id
        row = self._conn.execute("SELECT MAX(id) FROM paper_trades").fetchone()
        self._next_id = (row[0] or 0) + 1
        logger.info(f"PaperTrader: connected to {self._db_path}")

    def close(self) -> None:
        if self._conn:
            self._conn.close()

    # ------------------------------------------------------------------
    # Trade management
    # ------------------------------------------------------------------

    async def open_trade(self, signal: Signal, quantity: int) -> int | None:
        """Record a new paper trade opening. Returns the trade ID."""
        if not self._conn:
            self.connect()

        cost = signal.entry_price * quantity
        if cost > self._capital:
            logger.warning(f"PaperTrader: insufficient capital for {signal.symbol} (need {cost:.0f}, have {self._capital:.0f}).")
            return None

        self._capital -= cost
        trade_id = self._next_id
        self._next_id += 1

        record: dict[str, Any] = {
            "id": trade_id,
            "timestamp": datetime.now().isoformat(),
            "symbol": signal.symbol,
            "direction": signal.direction,
            "strategy": signal.strategy,
            "entry_price": signal.entry_price,
            "exit_price": None,
            "quantity": quantity,
            "stop_loss": signal.stop_loss,
            "target": signal.target_1,
            "pnl": None,
            "pnl_percent": None,
            "exit_reason": None,
            "holding_time_minutes": None,
            "status": "OPEN",
        }

        assert self._conn is not None
        self._conn.execute(
            """INSERT INTO paper_trades
            (id, timestamp, symbol, direction, strategy, entry_price, exit_price,
             quantity, stop_loss, target, pnl, pnl_percent, exit_reason,
             holding_time_minutes, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                record["id"], record["timestamp"], record["symbol"],
                record["direction"], record["strategy"], record["entry_price"],
                record["exit_price"], record["quantity"], record["stop_loss"],
                record["target"], record["pnl"], record["pnl_percent"],
                record["exit_reason"], record["holding_time_minutes"], record["status"],
            ),
        )
        self._conn.commit()
        self._open_trades[trade_id] = record
        logger.info(f"PaperTrade #{trade_id} opened: {signal.symbol} {signal.direction} {quantity}@{signal.entry_price:.2f}")
        return trade_id

    async def close_trade(
        self,
        trade_id: int,
        exit_price: float,
        reason: str = "MANUAL",
        holding_minutes: int = 0,
    ) -> dict[str, Any] | None:
        """Close a paper trade and update P&L."""
        if not self._conn:
            self.connect()

        record = self._open_trades.pop(trade_id, None)
        if record is None:
            # Try to load from DB
            assert self._conn is not None
            row = self._conn.execute("SELECT * FROM paper_trades WHERE id=?", (trade_id,)).fetchone()
            if row is None:
                logger.error(f"PaperTrader: trade #{trade_id} not found.")
                return None
            record = dict(row)

        qty = record["quantity"]
        entry = record["entry_price"]
        direction = record["direction"]

        if direction == "LONG":
            pnl = (exit_price - entry) * qty
        else:
            pnl = (entry - exit_price) * qty

        charges = _CHARGE_PCT * entry * qty + _CHARGE_PCT * exit_price * qty
        net_pnl = pnl - charges
        pnl_pct = pnl / (entry * qty) * 100

        # Restore capital + profit
        self._capital += entry * qty + net_pnl

        record.update({
            "exit_price": exit_price,
            "pnl": round(net_pnl, 2),
            "pnl_percent": round(pnl_pct, 2),
            "exit_reason": reason,
            "holding_time_minutes": holding_minutes,
            "status": "CLOSED",
        })

        assert self._conn is not None
        self._conn.execute(
            """UPDATE paper_trades SET exit_price=?, pnl=?, pnl_percent=?,
               exit_reason=?, holding_time_minutes=?, status=? WHERE id=?""",
            (
                record["exit_price"], record["pnl"], record["pnl_percent"],
                record["exit_reason"], record["holding_time_minutes"],
                record["status"], trade_id,
            ),
        )
        self._conn.commit()
        logger.info(f"PaperTrade #{trade_id} closed: {record['symbol']} P&L={net_pnl:.2f} ({pnl_pct:.2f}%)")
        return record

    async def simulate_fill(self, symbol: str, ltp: float) -> None:
        """Check open trades against LTP and auto-close if SL/target hit."""
        for tid, rec in list(self._open_trades.items()):
            if rec["symbol"] != symbol:
                continue
            direction = rec["direction"]
            sl = rec["stop_loss"]
            target = rec["target"]

            if direction == "LONG":
                if ltp <= sl:
                    await self.close_trade(tid, ltp, "SL_HIT")
                elif target and ltp >= target:
                    await self.close_trade(tid, ltp, "TARGET_HIT")
            else:
                if ltp >= sl:
                    await self.close_trade(tid, ltp, "SL_HIT")
                elif target and ltp <= target:
                    await self.close_trade(tid, ltp, "TARGET_HIT")

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_open_trades(self) -> list[dict[str, Any]]:
        return list(self._open_trades.values())

    def get_today_trades(self) -> list[dict[str, Any]]:
        if not self._conn:
            return []
        today = date.today().isoformat()
        rows = self._conn.execute(
            "SELECT * FROM paper_trades WHERE timestamp LIKE ?", (f"{today}%",)
        ).fetchall()
        return [dict(r) for r in rows]

    def generate_daily_summary(self) -> dict[str, Any]:
        """Compute and persist today's summary."""
        trades = self.get_today_trades()
        closed = [t for t in trades if t["status"] == "CLOSED"]

        total = len(closed)
        winning = sum(1 for t in closed if (t["pnl"] or 0) > 0)
        losing = total - winning
        gross_pnl = sum((t["pnl"] or 0) for t in closed)
        charges = sum(
            _CHARGE_PCT * (t["entry_price"] * t["quantity"]) * 2 for t in closed
        )
        net_pnl = gross_pnl  # already net in close_trade
        capital_used = self._initial_capital - self._capital

        # Max drawdown (simple equity curve)
        running = self._initial_capital
        peak = running
        max_dd = 0.0
        for t in closed:
            running += (t["pnl"] or 0)
            peak = max(peak, running)
            dd = (peak - running) / peak * 100
            max_dd = max(max_dd, dd)

        summary: dict[str, Any] = {
            "date": date.today().isoformat(),
            "total_trades": total,
            "winning_trades": winning,
            "losing_trades": losing,
            "gross_pnl": round(gross_pnl, 2),
            "charges_estimate": round(charges, 2),
            "net_pnl": round(net_pnl, 2),
            "max_drawdown": round(max_dd, 2),
            "capital_used": round(capital_used, 2),
        }

        if self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO paper_daily_summary
                   (date, total_trades, winning_trades, losing_trades,
                    gross_pnl, charges_estimate, net_pnl, max_drawdown, capital_used)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                tuple(summary.values()),
            )
            self._conn.commit()

        return summary

    @property
    def capital(self) -> float:
        return self._capital
