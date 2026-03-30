"""Portfolio state tracker – positions, P&L, drawdown."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger


@dataclass
class Position:
    """Represents a single open position."""

    symbol: str
    direction: str       # LONG | SHORT
    quantity: int
    entry_price: float
    current_price: float
    entry_time: datetime = field(default_factory=datetime.now)

    @property
    def unrealised_pnl(self) -> float:
        if self.direction == "LONG":
            return (self.current_price - self.entry_price) * self.quantity
        return (self.entry_price - self.current_price) * self.quantity

    @property
    def unrealised_pnl_pct(self) -> float:
        cost = self.entry_price * self.quantity
        return self.unrealised_pnl / cost * 100 if cost else 0.0


class Portfolio:
    """Tracks capital, open positions, realised P&L, and drawdown.

    Capital flow:
    - ``add_position()`` deducts the cost from available capital
    - ``close_position()`` restores capital + realised profit/loss
    """

    def __init__(self, initial_capital: float = 100000.0) -> None:
        self._initial_capital = initial_capital
        self._available_capital = initial_capital
        self._open_positions: dict[str, Position] = {}
        self._realised_pnl: float = 0.0
        self._peak_value: float = initial_capital

    # ------------------------------------------------------------------
    # Position management
    # ------------------------------------------------------------------

    def add_position(
        self,
        symbol: str,
        quantity: int,
        entry_price: float,
        direction: str,
    ) -> None:
        """Record a new open position and deduct cost from available capital."""
        cost = entry_price * quantity
        if cost > self._available_capital:
            logger.warning(
                f"Portfolio: insufficient capital for {symbol} "
                f"(need {cost:.0f}, have {self._available_capital:.0f})"
            )
        self._available_capital -= cost
        self._open_positions[symbol] = Position(
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            entry_price=entry_price,
            current_price=entry_price,
        )
        logger.debug(f"Portfolio: position added {symbol} {direction} {quantity}@{entry_price:.2f}")

    def close_position(self, symbol: str, exit_price: float) -> float:
        """Close a position, update realised P&L, and return cash to capital."""
        pos = self._open_positions.pop(symbol, None)
        if pos is None:
            logger.warning(f"Portfolio: close_position called for unknown symbol {symbol}.")
            return 0.0

        if pos.direction == "LONG":
            pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - exit_price) * pos.quantity

        self._realised_pnl += pnl
        self._available_capital += pos.entry_price * pos.quantity + pnl
        total_value = self.total_value
        self._peak_value = max(self._peak_value, total_value)
        logger.debug(f"Portfolio: position closed {symbol} exit={exit_price:.2f} pnl={pnl:.2f}")
        return pnl

    def update_price(self, symbol: str, price: float) -> None:
        """Update the mark-to-market price for an open position."""
        pos = self._open_positions.get(symbol)
        if pos:
            pos.current_price = price

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    @property
    def open_positions(self) -> dict[str, Position]:
        return self._open_positions

    @property
    def available_capital(self) -> float:
        return self._available_capital

    @property
    def realised_pnl(self) -> float:
        return self._realised_pnl

    @property
    def unrealised_pnl(self) -> float:
        return sum(p.unrealised_pnl for p in self._open_positions.values())

    @property
    def total_pnl(self) -> float:
        return self._realised_pnl + self.unrealised_pnl

    @property
    def total_value(self) -> float:
        """Total portfolio value: available capital + cost of open positions + unrealised P&L."""
        open_value = sum(p.current_price * p.quantity for p in self._open_positions.values())
        return self._available_capital + open_value

    @property
    def drawdown_pct(self) -> float:
        """Current drawdown from peak, in percent."""
        if self._peak_value == 0:
            return 0.0
        return (self._peak_value - self.total_value) / self._peak_value * 100

    def to_dict(self) -> dict[str, Any]:
        """Serialize portfolio state for display / API."""
        return {
            "initial_capital": self._initial_capital,
            "available_capital": round(self._available_capital, 2),
            "total_value": round(self.total_value, 2),
            "realised_pnl": round(self._realised_pnl, 2),
            "unrealised_pnl": round(self.unrealised_pnl, 2),
            "total_pnl": round(self.total_pnl, 2),
            "drawdown_pct": round(self.drawdown_pct, 2),
            "open_positions": [
                {
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "unrealised_pnl": round(p.unrealised_pnl, 2),
                    "unrealised_pnl_pct": round(p.unrealised_pnl_pct, 2),
                }
                for p in self._open_positions.values()
            ],
        }
