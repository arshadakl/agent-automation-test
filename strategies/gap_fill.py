"""Gap Fill strategy – 5-minute timeframe."""

from __future__ import annotations

from datetime import datetime, time

import pandas as pd
from loguru import logger

from strategies.base_strategy import BaseStrategy, Signal


class GapFillStrategy(BaseStrategy):
    """Gap Fill (gap-and-fill mean reversion).

    - Timeframe: 5m
    - Gap up: today's open > prev close by ≥ *gap_threshold_pct*.
      If price starts reversing (close < open of gap candle), go SHORT
      toward prev close.
    - Gap down: today's open < prev close by ≥ *gap_threshold_pct*.
      If price starts filling, go LONG toward prev close.
    - Only in the first 30 minutes after market open (09:15–09:45).
    - Stop: 0.5% beyond gap open level.
    - Target: previous close (full gap fill).
    """

    name = "gap_fill"
    timeframe = "5m"
    required_history = 5

    GAP_WINDOW_MINUTES: int = 30
    MARKET_OPEN: time = time(9, 15)

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self._gap_threshold: float = self.params.get("gap_threshold_pct", 1.0) / 100
        self._sl_pct: float = self.params.get("sl_pct", 0.5) / 100
        self._signalled_today: set[str] = set()  # prevent multiple signals

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if len(candles) < self.required_history:
            return None

        symbol: str = tick.get("symbol", "")
        ts = tick.get("timestamp") or datetime.now()
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)

        # Only trade in first 30 minutes
        current_time = ts.time() if hasattr(ts, "time") else datetime.now().time()
        if current_time > time(9, 45) or current_time < self.MARKET_OPEN:
            return None

        # Reset daily tracking at start of day
        today_str = ts.strftime("%Y-%m-%d")
        key = f"{symbol}_{today_str}"

        if key in self._signalled_today:
            return None

        df = candles.copy().reset_index(drop=True)

        # Identify today vs previous day candles
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"])
            today = df["datetime"].iloc[-1].date()
            today_mask = df["datetime"].dt.date == today
            prev_mask = df["datetime"].dt.date < today
            today_df = df[today_mask]
            prev_df = df[prev_mask]
        else:
            # Fallback: assume first few candles are today
            today_df = df.tail(4)
            prev_df = df.head(len(df) - 4)

        if prev_df.empty or today_df.empty:
            return None

        prev_close = float(prev_df.iloc[-1]["close"])
        gap_open = float(today_df.iloc[0]["open"])
        gap_pct = (gap_open - prev_close) / prev_close

        latest = today_df.iloc[-1]
        close = float(latest["close"])
        day_open = float(today_df.iloc[0]["open"])

        # Gap UP – look for short (price filling back down)
        if gap_pct >= self._gap_threshold:
            # Price has started filling: close < day's first candle open
            if close < day_open:
                sl = round(gap_open * (1 + self._sl_pct), 2)
                target = round(prev_close, 2)
                if target >= close:
                    return None
                self._signalled_today.add(key)
                return Signal(
                    strategy=self.name,
                    symbol=symbol,
                    direction="SHORT",
                    entry_price=round(close, 2),
                    stop_loss=sl,
                    target_1=target,
                    target_2=None,
                    confidence=min(round(gap_pct / 0.02, 2), 1.0),
                    timestamp=ts,
                    metadata={"gap_pct": round(gap_pct * 100, 2), "prev_close": prev_close, "gap_open": gap_open},
                )

        # Gap DOWN – look for long (price filling back up)
        elif gap_pct <= -self._gap_threshold:
            if close > day_open:
                sl = round(gap_open * (1 - self._sl_pct), 2)
                target = round(prev_close, 2)
                if target <= close:
                    return None
                self._signalled_today.add(key)
                return Signal(
                    strategy=self.name,
                    symbol=symbol,
                    direction="LONG",
                    entry_price=round(close, 2),
                    stop_loss=sl,
                    target_1=target,
                    target_2=None,
                    confidence=min(round(abs(gap_pct) / 0.02, 2), 1.0),
                    timestamp=ts,
                    metadata={"gap_pct": round(gap_pct * 100, 2), "prev_close": prev_close, "gap_open": gap_open},
                )

        return None

    def get_stop_loss(self, entry_price: float, signal: Signal) -> float:
        return signal.stop_loss

    def get_targets(self, entry_price: float, signal: Signal) -> list[float]:
        return [signal.target_1]

    def backtest_params(self) -> dict:
        return {
            "gap_threshold_pct": [0.5, 1.0, 1.5, 2.0],
            "sl_pct": [0.3, 0.5, 0.75],
        }

    def reset_daily(self) -> None:
        """Clear the daily signal tracker (call at start of each session)."""
        self._signalled_today.clear()
