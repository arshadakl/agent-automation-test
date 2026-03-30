"""Vectorized backtest engine with Indian market charges."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from strategies.base_strategy import BaseStrategy, Signal


# ---------------------------------------------------------------------------
# Indian market charges (per transaction, applied on turnover)
# ---------------------------------------------------------------------------
_BROKERAGE_MAX = 20.0          # ₹ 20 per order cap
_BROKERAGE_PCT = 0.0003        # 0.03%
_STT_SELL = 0.00025            # 0.025% on sell side
_EXCHANGE_TXN = 0.0000345      # 0.00345%
_SEBI_CHARGES = 0.000001       # 0.0001%
_GST_RATE = 0.18               # 18% on (brokerage + exchange + sebi)
_STAMP_DUTY_BUY = 0.00003      # 0.003% on buy side


def _calc_charges(turnover_buy: float, turnover_sell: float) -> float:
    """Return total charges for a round-trip trade."""
    brokerage = min(_BROKERAGE_PCT * turnover_buy, _BROKERAGE_MAX) + min(
        _BROKERAGE_PCT * turnover_sell, _BROKERAGE_MAX
    )
    stt = _STT_SELL * turnover_sell
    exchange = _EXCHANGE_TXN * (turnover_buy + turnover_sell)
    sebi = _SEBI_CHARGES * (turnover_buy + turnover_sell)
    gst = _GST_RATE * (brokerage + exchange + sebi)
    stamp = _STAMP_DUTY_BUY * turnover_buy
    return brokerage + stt + exchange + sebi + gst + stamp


@dataclass
class TradeRecord:
    """Single trade result from backtesting."""

    symbol: str
    strategy: str
    direction: str
    entry_date: datetime
    exit_date: datetime | None
    entry_price: float
    exit_price: float
    quantity: int
    pnl_gross: float
    charges: float
    pnl_net: float
    exit_reason: str
    holding_bars: int


@dataclass
class BacktestResult:
    """Aggregated backtest results."""

    strategy_name: str
    symbol: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=pd.Series)

    # Computed metrics
    total_return_pct: float = 0.0
    cagr: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_pnl: float = 0.0


class BacktestEngine:
    """Walk-forward, event-driven backtest engine.

    Usage:
        engine = BacktestEngine(strategy, initial_capital=100_000)
        result = engine.run(ohlcv_df, slippage_pct=0.0005)
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        initial_capital: float = 100_000.0,
        slippage_pct: float = 0.0005,
        per_trade_risk_pct: float = 0.015,
        max_position_pct: float = 0.15,
    ) -> None:
        self._strategy = strategy
        self._capital = initial_capital
        self._initial_capital = initial_capital
        self._slippage = slippage_pct
        self._risk_pct = per_trade_risk_pct
        self._max_pos_pct = max_position_pct

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, df: pd.DataFrame, symbol: str = "UNKNOWN") -> BacktestResult:
        """Run the strategy over *df* and return a BacktestResult."""
        df = df.copy().reset_index(drop=True)
        self._capital = self._initial_capital
        trades: list[TradeRecord] = []
        equity: list[float] = []

        open_trade: dict[str, Any] | None = None

        for i in range(self._strategy.required_history, len(df)):
            candles = df.iloc[: i + 1]
            latest = df.iloc[i]
            ts = latest.get("datetime", pd.Timestamp(i))
            close = float(latest["close"])

            tick = {"symbol": symbol, "ltp": close, "timestamp": ts}

            # ----- Check exit conditions for open trade -----
            if open_trade is not None:
                exit_price, exit_reason = self._check_exit(open_trade, latest)
                if exit_price is not None:
                    trade = self._close_trade(open_trade, exit_price, exit_reason, i)
                    trades.append(trade)
                    self._capital += trade.pnl_net
                    open_trade = None

            # ----- Generate new signal -----
            if open_trade is None:
                try:
                    signal = self._strategy.generate_signal(candles, tick)
                except Exception as exc:
                    logger.debug(f"BacktestEngine: signal error at bar {i}: {exc}")
                    signal = None

                if signal is not None:
                    qty = self._position_size(signal)
                    if qty > 0:
                        entry_with_slip = self._apply_slippage(signal.entry_price, signal.direction)
                        open_trade = {
                            "signal": signal,
                            "entry_price": entry_with_slip,
                            "quantity": qty,
                            "entry_bar": i,
                            "entry_date": ts,
                        }

            equity.append(self._capital + (
                (close - open_trade["entry_price"]) * open_trade["quantity"]
                if open_trade and open_trade["signal"].direction == "LONG"
                else (open_trade["entry_price"] - close) * open_trade["quantity"]
                if open_trade else 0.0
            ))

        # Force-close any remaining open trade
        if open_trade is not None and not df.empty:
            last_close = float(df.iloc[-1]["close"])
            trade = self._close_trade(open_trade, last_close, "END_OF_DATA", len(df) - 1)
            trades.append(trade)
            self._capital += trade.pnl_net

        result = self._compute_metrics(trades, equity, df, symbol)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_exit(
        self, trade: dict, candle: pd.Series
    ) -> tuple[float | None, str]:
        """Check if SL or target is hit on this candle."""
        signal: Signal = trade["signal"]
        direction = signal.direction
        low = float(candle["low"])
        high = float(candle["high"])

        if direction == "LONG":
            if low <= signal.stop_loss:
                return signal.stop_loss, "SL_HIT"
            if signal.target_1 and high >= signal.target_1:
                return signal.target_1, "TARGET_HIT"
        else:
            if high >= signal.stop_loss:
                return signal.stop_loss, "SL_HIT"
            if signal.target_1 and low <= signal.target_1:
                return signal.target_1, "TARGET_HIT"

        return None, ""

    def _close_trade(
        self, trade: dict, exit_price: float, reason: str, exit_bar: int
    ) -> TradeRecord:
        signal: Signal = trade["signal"]
        qty = trade["quantity"]
        entry = trade["entry_price"]

        exit_price = self._apply_slippage(exit_price, "SELL" if signal.direction == "LONG" else "BUY")

        if signal.direction == "LONG":
            gross = (exit_price - entry) * qty
        else:
            gross = (entry - exit_price) * qty

        charges = _calc_charges(entry * qty, exit_price * qty)
        net = gross - charges

        return TradeRecord(
            symbol=trade["signal"].symbol,
            strategy=signal.strategy,
            direction=signal.direction,
            entry_date=trade["entry_date"],
            exit_date=None,
            entry_price=entry,
            exit_price=exit_price,
            quantity=qty,
            pnl_gross=round(gross, 2),
            charges=round(charges, 2),
            pnl_net=round(net, 2),
            exit_reason=reason,
            holding_bars=exit_bar - trade["entry_bar"],
        )

    def _apply_slippage(self, price: float, direction: str) -> float:
        if direction in ("BUY", "LONG"):
            return price * (1 + self._slippage)
        return price * (1 - self._slippage)

    def _position_size(self, signal: Signal) -> int:
        risk_amount = self._capital * self._risk_pct
        sl_dist = abs(signal.entry_price - signal.stop_loss)
        if sl_dist == 0:
            return 0
        qty_risk = int(risk_amount / sl_dist)
        max_qty = int(self._capital * self._max_pos_pct / signal.entry_price)
        return max(min(qty_risk, max_qty), 0)

    def _compute_metrics(
        self,
        trades: list[TradeRecord],
        equity: list[float],
        df: pd.DataFrame,
        symbol: str,
    ) -> BacktestResult:
        equity_s = pd.Series(equity, name="equity")

        # Drawdown
        peak = equity_s.cummax()
        dd = (peak - equity_s) / peak * 100
        max_dd = float(dd.max()) if not dd.empty else 0.0

        # Returns
        final_capital = equity_s.iloc[-1] if not equity_s.empty else self._initial_capital
        total_ret_pct = (final_capital - self._initial_capital) / self._initial_capital * 100

        # CAGR
        n_days = len(df)
        years = max(n_days / 252, 0.01)
        cagr = ((final_capital / self._initial_capital) ** (1 / years) - 1) * 100

        # Sharpe (daily returns)
        daily_ret = equity_s.pct_change().dropna()
        sharpe = (daily_ret.mean() / daily_ret.std() * (252 ** 0.5)) if daily_ret.std() > 0 else 0.0

        winning = [t for t in trades if t.pnl_net > 0]
        losing = [t for t in trades if t.pnl_net <= 0]
        win_rate = len(winning) / len(trades) * 100 if trades else 0.0
        gross_profit = sum(t.pnl_net for t in winning)
        gross_loss = abs(sum(t.pnl_net for t in losing))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        start_date = df["datetime"].iloc[0] if "datetime" in df.columns else datetime.now()
        end_date = df["datetime"].iloc[-1] if "datetime" in df.columns else datetime.now()

        result = BacktestResult(
            strategy_name=self._strategy.name,
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=self._initial_capital,
            trades=trades,
            equity_curve=equity_s,
            total_return_pct=round(total_ret_pct, 2),
            cagr=round(cagr, 2),
            sharpe_ratio=round(float(sharpe), 2),
            max_drawdown_pct=round(max_dd, 2),
            win_rate=round(win_rate, 2),
            profit_factor=round(profit_factor, 2),
            total_trades=len(trades),
            avg_pnl=round(sum(t.pnl_net for t in trades) / len(trades), 2) if trades else 0.0,
        )
        return result
