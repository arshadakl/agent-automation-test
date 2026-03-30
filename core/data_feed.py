"""Real-time data feed manager – builds OHLCV candles from WebSocket ticks."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd
from loguru import logger


class DataFeed:
    """Manages WebSocket connections and builds OHLCV candles from ticks.

    Supported timeframes: "1m", "5m", "15m".
    Candle history per (symbol, timeframe) is kept as a DataFrame.
    Callbacks are fired whenever a candle is **closed** (i.e., when a new
    candle period starts).
    """

    TIMEFRAME_MINUTES: dict[str, int] = {"1m": 1, "5m": 5, "15m": 15}

    def __init__(self, broker: "AngelBroker | None" = None) -> None:  # noqa: F821
        self._broker = broker
        # tick_buffer[symbol] = list of raw tick dicts (within current minute)
        self._tick_buffer: dict[str, list[dict]] = defaultdict(list)
        # candle_history[symbol][timeframe] = pd.DataFrame
        self._candle_history: dict[str, dict[str, pd.DataFrame]] = defaultdict(
            lambda: {tf: pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])
                     for tf in self.TIMEFRAME_MINUTES}
        )
        # current (incomplete) 1-min candle per symbol
        self._current_1m: dict[str, dict | None] = {}
        # callbacks: list of (timeframe, callable)
        self._candle_callbacks: list[tuple[str, Callable]] = []
        self._running = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def subscribe(self, symbols: list[str]) -> None:
        """Mark symbols for which tick data will be processed."""
        for sym in symbols:
            if sym not in self._current_1m:
                self._current_1m[sym] = None

    def on_new_candle(self, timeframe: str, callback: Callable[[str, pd.Series], None]) -> None:
        """Register a callback invoked with (symbol, closed_candle_series) for *timeframe*."""
        self._candle_callbacks.append((timeframe, callback))

    def process_tick(self, tick: dict) -> None:
        """Process a single incoming tick and update candle state."""
        symbol: str = tick.get("symbol", tick.get("trading_symbol", ""))
        ltp: float = float(tick.get("last_traded_price", tick.get("ltp", 0)) or 0)
        volume: int = int(tick.get("volume_trade_for_the_day", tick.get("volume", 0)) or 0)
        ts_raw = tick.get("exchange_timestamp", tick.get("timestamp"))

        if not symbol or ltp == 0:
            return

        try:
            if isinstance(ts_raw, (int, float)):
                ts = datetime.fromtimestamp(ts_raw / 1000)
            elif isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw)
            else:
                ts = datetime.now()
        except Exception:
            ts = datetime.now()

        # Determine which 1-minute bucket this tick belongs to
        candle_ts = ts.replace(second=0, microsecond=0)

        cur = self._current_1m.get(symbol)

        if cur is None or cur["datetime"] != candle_ts:
            # Close previous candle if it exists
            if cur is not None:
                self._close_candle(symbol, cur)
            # Start a new candle
            self._current_1m[symbol] = {
                "datetime": candle_ts,
                "open": ltp,
                "high": ltp,
                "low": ltp,
                "close": ltp,
                "volume": volume,
            }
        else:
            # Update current candle
            cur["high"] = max(cur["high"], ltp)
            cur["low"] = min(cur["low"], ltp)
            cur["close"] = ltp
            cur["volume"] = volume

    def get_candles(self, symbol: str, timeframe: str) -> pd.DataFrame:
        """Return the candle history for *symbol* and *timeframe*."""
        return self._candle_history[symbol][timeframe].copy()

    def get_latest_candle(self, symbol: str, timeframe: str) -> pd.Series | None:
        """Return the most recent closed candle."""
        df = self._candle_history[symbol][timeframe]
        if df.empty:
            return None
        return df.iloc[-1]

    def inject_historical(self, symbol: str, timeframe: str, df: pd.DataFrame) -> None:
        """Pre-load historical candles (e.g., from API at startup)."""
        self._candle_history[symbol][timeframe] = df.copy()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _close_candle(self, symbol: str, candle: dict) -> None:
        """Append a completed 1-minute candle and aggregate to higher TFs."""
        candle_row = pd.DataFrame([candle])
        candle_row["datetime"] = pd.to_datetime(candle_row["datetime"])

        # Update 1m history
        self._candle_history[symbol]["1m"] = pd.concat(
            [self._candle_history[symbol]["1m"], candle_row], ignore_index=True
        ).tail(1000)

        self._fire_callbacks("1m", symbol, pd.Series(candle))

        # Aggregate to 5m and 15m
        for tf, minutes in [("5m", 5), ("15m", 15)]:
            dt = candle["datetime"]
            if dt.minute % minutes == (minutes - 1) or (minutes == 15 and dt.minute % 15 == 14):
                self._aggregate_to_tf(symbol, tf, minutes)

    def _aggregate_to_tf(self, symbol: str, tf: str, minutes: int) -> None:
        """Build a higher-TF candle from completed 1m candles."""
        df_1m = self._candle_history[symbol]["1m"]
        if df_1m.empty:
            return

        latest_ts = df_1m.iloc[-1]["datetime"]
        # Floor the minute to the start of the TF bucket
        bucket_start = latest_ts - timedelta(minutes=(latest_ts.minute % minutes))
        bucket_start = bucket_start.replace(second=0, microsecond=0)
        bucket_end = bucket_start + timedelta(minutes=minutes)

        bucket = df_1m[
            (df_1m["datetime"] >= bucket_start) & (df_1m["datetime"] < bucket_end)
        ]
        if bucket.empty:
            return

        agg = {
            "datetime": bucket_start,
            "open": bucket.iloc[0]["open"],
            "high": bucket["high"].max(),
            "low": bucket["low"].min(),
            "close": bucket.iloc[-1]["close"],
            "volume": bucket["volume"].max(),  # daily cumulative; take last
        }
        agg_row = pd.DataFrame([agg])
        agg_row["datetime"] = pd.to_datetime(agg_row["datetime"])

        self._candle_history[symbol][tf] = pd.concat(
            [self._candle_history[symbol][tf], agg_row], ignore_index=True
        ).tail(500)

        self._fire_callbacks(tf, symbol, pd.Series(agg))

    def _fire_callbacks(self, timeframe: str, symbol: str, candle: pd.Series) -> None:
        for tf, cb in self._candle_callbacks:
            if tf == timeframe:
                try:
                    cb(symbol, candle)
                except Exception as exc:
                    logger.error(f"Candle callback error [{tf}|{symbol}]: {exc}")

    # ------------------------------------------------------------------
    # Live feed loop
    # ------------------------------------------------------------------

    async def start(self, symbol_tokens: list[dict[str, str]]) -> None:
        """Start the WebSocket feed. Ticks are routed via process_tick()."""
        if self._broker is None:
            logger.warning("DataFeed: no broker attached – tick feed disabled.")
            return

        self._running = True
        logger.info("DataFeed: starting WebSocket subscription…")

        def tick_callback(message: dict) -> None:
            if isinstance(message, list):
                for tick in message:
                    self.process_tick(tick)
            elif isinstance(message, dict):
                self.process_tick(message)

        await self._broker.subscribe_ticks(symbol_tokens, tick_callback)

    def stop(self) -> None:
        """Stop the data feed."""
        self._running = False
        logger.info("DataFeed: stopped.")
