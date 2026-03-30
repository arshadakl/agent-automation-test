"""Trade execution engine – places, tracks, and manages orders."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from core.broker import AngelBroker, Order
from strategies.base_strategy import Signal


@dataclass
class OpenTrade:
    """Tracks the lifecycle of a single open trade."""

    signal: Signal
    entry_order_id: str
    sl_order_id: str = ""
    target_order_id: str = ""
    quantity: int = 0
    filled_qty: int = 0
    filled_price: float = 0.0
    status: str = "PENDING"  # PENDING | OPEN | PARTIAL | CLOSED
    entry_time: datetime = field(default_factory=datetime.now)
    trailing_sl: float = 0.0
    highest_price: float = 0.0  # for trailing SL tracking (long)
    lowest_price: float = 0.0   # for trailing SL tracking (short)


class ExecutionEngine:
    """Executes trading signals and manages open trades.

    Flow:
    1. ``execute_signal()`` → place entry order → place SL and target orders
    2. ``update_tick()`` → check trailing stops, close trades
    3. ``square_off_all()`` → emergency close of all positions
    """

    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 2.0

    def __init__(
        self,
        broker: AngelBroker,
        risk_manager: "RiskManager",  # noqa: F821
        portfolio: "Portfolio",  # noqa: F821
        config: dict[str, Any],
        paper_trader: "PaperTrader | None" = None,  # noqa: F821
    ) -> None:
        self._broker = broker
        self._risk = risk_manager
        self._portfolio = portfolio
        self._cfg = config
        self._paper_trader = paper_trader
        self._open_trades: dict[str, OpenTrade] = {}  # symbol → OpenTrade
        self._is_paper: bool = config.get("mode", "paper") == "paper"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute_signal(self, signal: Signal, quantity: int) -> bool:
        """Place entry, SL, and target orders for *signal*."""
        if signal.symbol in self._open_trades:
            logger.warning(f"ExecutionEngine: already have trade for {signal.symbol}.")
            return False

        token = signal.metadata.get("token", "")
        exchange = signal.metadata.get("exchange", "NSE")
        txn = "BUY" if signal.direction == "LONG" else "SELL"

        entry_order = Order(
            symbol=signal.symbol,
            token=token,
            exchange=exchange,
            transaction_type=txn,
            quantity=quantity,
            order_type="MARKET",
            product_type="INTRADAY",
        )

        if self._is_paper and self._paper_trader:
            trade_id = await self._paper_trader.open_trade(signal, quantity)
            if trade_id:
                ot = OpenTrade(
                    signal=signal,
                    entry_order_id=str(trade_id),
                    quantity=quantity,
                    filled_qty=quantity,
                    filled_price=signal.entry_price,
                    status="OPEN",
                    trailing_sl=signal.stop_loss,
                    highest_price=signal.entry_price,
                    lowest_price=signal.entry_price,
                )
                self._open_trades[signal.symbol] = ot
                self._portfolio.add_position(signal.symbol, quantity, signal.entry_price, signal.direction)
                logger.info(f"PaperTrade opened: {signal.symbol} {signal.direction} qty={quantity} @ {signal.entry_price}")
                return True
            return False

        # Live trading
        entry_id = await self._place_with_retry(entry_order)
        if not entry_id:
            logger.error(f"ExecutionEngine: entry order failed for {signal.symbol}.")
            return False

        ot = OpenTrade(
            signal=signal,
            entry_order_id=entry_id,
            quantity=quantity,
            filled_qty=quantity,
            filled_price=signal.entry_price,
            status="OPEN",
            trailing_sl=signal.stop_loss,
            highest_price=signal.entry_price,
            lowest_price=signal.entry_price,
        )

        # SL order
        sl_txn = "SELL" if signal.direction == "LONG" else "BUY"
        sl_order = Order(
            symbol=signal.symbol,
            token=token,
            exchange=exchange,
            transaction_type=sl_txn,
            quantity=quantity,
            order_type="SL-M",
            product_type="INTRADAY",
            trigger_price=signal.stop_loss,
            variety="STOPLOSS",
        )
        sl_id = await self._place_with_retry(sl_order)
        ot.sl_order_id = sl_id

        # Target order
        if signal.target_1:
            tgt_order = Order(
                symbol=signal.symbol,
                token=token,
                exchange=exchange,
                transaction_type=sl_txn,
                quantity=quantity,
                order_type="LIMIT",
                product_type="INTRADAY",
                price=signal.target_1,
            )
            tgt_id = await self._place_with_retry(tgt_order)
            ot.target_order_id = tgt_id

        self._open_trades[signal.symbol] = ot
        self._portfolio.add_position(signal.symbol, quantity, signal.entry_price, signal.direction)
        logger.info(f"Trade opened: {signal.symbol} {signal.direction} qty={quantity} @ {signal.entry_price}")
        return True

    async def update_tick(self, symbol: str, ltp: float) -> None:
        """Update trailing stops and check if trade should be closed."""
        ot = self._open_trades.get(symbol)
        if ot is None or ot.status != "OPEN":
            return

        trail_cfg = self._cfg.get("risk", {})
        trailing_enabled = trail_cfg.get("trailing_stop_enabled", True)
        trigger_pct = trail_cfg.get("trailing_stop_trigger_percent", 1.0) / 100
        distance_pct = trail_cfg.get("trailing_stop_distance_percent", 0.5) / 100

        if ot.signal.direction == "LONG":
            # Track highest
            if ltp > ot.highest_price:
                ot.highest_price = ltp
                if trailing_enabled:
                    gain_pct = (ltp - ot.filled_price) / ot.filled_price
                    if gain_pct >= trigger_pct:
                        new_sl = ltp * (1 - distance_pct)
                        if new_sl > ot.trailing_sl:
                            await self._update_trailing_sl(ot, new_sl)

            # SL hit?
            if ltp <= ot.trailing_sl:
                await self.close_trade(symbol, ltp, "SL_HIT")

            # Target hit?
            if ot.signal.target_1 and ltp >= ot.signal.target_1:
                await self.close_trade(symbol, ltp, "TARGET_HIT")

        elif ot.signal.direction == "SHORT":
            if ltp < ot.lowest_price:
                ot.lowest_price = ltp
                if trailing_enabled:
                    gain_pct = (ot.filled_price - ltp) / ot.filled_price
                    if gain_pct >= trigger_pct:
                        new_sl = ltp * (1 + distance_pct)
                        if new_sl < ot.trailing_sl:
                            await self._update_trailing_sl(ot, new_sl)

            if ltp >= ot.trailing_sl:
                await self.close_trade(symbol, ltp, "SL_HIT")
            if ot.signal.target_1 and ltp <= ot.signal.target_1:
                await self.close_trade(symbol, ltp, "TARGET_HIT")

        self._portfolio.update_price(symbol, ltp)

    async def close_trade(self, symbol: str, exit_price: float, reason: str = "MANUAL") -> None:
        """Close a trade at *exit_price*."""
        ot = self._open_trades.pop(symbol, None)
        if ot is None:
            return

        ot.status = "CLOSED"
        qty = ot.filled_qty
        entry = ot.filled_price
        direction = ot.signal.direction

        if direction == "LONG":
            pnl = (exit_price - entry) * qty
        else:
            pnl = (entry - exit_price) * qty

        self._risk.record_trade_result(pnl)
        self._portfolio.close_position(symbol, exit_price)

        if self._is_paper and self._paper_trader:
            holding_mins = int((datetime.now() - ot.entry_time).total_seconds() / 60)
            await self._paper_trader.close_trade(
                trade_id=int(ot.entry_order_id) if ot.entry_order_id.isdigit() else 0,
                exit_price=exit_price,
                reason=reason,
                holding_minutes=holding_mins,
            )
        else:
            # Cancel opposite leg on live
            txn = "SELL" if direction == "LONG" else "BUY"
            close_order = Order(
                symbol=symbol,
                token=ot.signal.metadata.get("token", ""),
                exchange=ot.signal.metadata.get("exchange", "NSE"),
                transaction_type=txn,
                quantity=qty,
                order_type="MARKET",
                product_type="INTRADAY",
            )
            await self._place_with_retry(close_order)
            if ot.sl_order_id:
                await self._broker.cancel_order(ot.sl_order_id)
            if ot.target_order_id:
                await self._broker.cancel_order(ot.target_order_id)

        logger.info(f"Trade closed: {symbol} P&L={pnl:.2f} reason={reason}")

    async def square_off_all(self, reason: str = "FORCE_SQUAREOFF") -> None:
        """Emergency square-off of all open positions."""
        symbols = list(self._open_trades.keys())
        logger.warning(f"ExecutionEngine: squaring off {len(symbols)} positions – {reason}")
        for sym in symbols:
            ltps = await self._broker.get_ltp([{"symbol": sym, "token": self._open_trades[sym].signal.metadata.get("token", "")}])
            ltp = ltps.get(sym, self._open_trades[sym].filled_price)
            await self.close_trade(sym, ltp, reason)

    @property
    def open_trades(self) -> dict[str, OpenTrade]:
        return self._open_trades

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _place_with_retry(self, order: Order) -> str:
        """Place order with retry on failure."""
        for attempt in range(1, self.MAX_RETRIES + 1):
            order_id = await self._broker.place_order(order)
            if order_id:
                return order_id
            if attempt < self.MAX_RETRIES:
                logger.warning(f"ExecutionEngine: order attempt {attempt} failed, retrying…")
                await asyncio.sleep(self.RETRY_DELAY_SECONDS)
        logger.error(f"ExecutionEngine: order failed after {self.MAX_RETRIES} attempts.")
        return ""

    async def _update_trailing_sl(self, ot: OpenTrade, new_sl: float) -> None:
        """Update the trailing SL order."""
        ot.trailing_sl = new_sl
        if ot.sl_order_id and not self._is_paper:
            await self._broker.modify_order(
                ot.sl_order_id,
                {"triggerprice": str(round(new_sl, 2)), "variety": "STOPLOSS"},
            )
        logger.debug(f"Trailing SL updated for {ot.signal.symbol}: {new_sl:.2f}")
