"""Pre-market stock scanner – filters NSE stocks by volume, ATR, price."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import numpy as np
from loguru import logger


class Scanner:
    """Scans the configured stock universe and returns the best candidates.

    Filters applied (in order):
    1. Price range – ``[price_min, price_max]``
    2. Minimum 20-day average volume
    3. Minimum ATR% (ATR / price * 100)
    4. Minimum turnover (avg_volume * avg_price ≥ min_turnover_cr * 1e7)

    Results are sorted by ATR% descending and the top *max_stocks* are returned.
    """

    def __init__(self, config: dict[str, Any], broker: "AngelBroker | None" = None) -> None:  # noqa: F821
        self._cfg = config
        self._broker = broker
        self._universe: list[dict[str, str]] = []

    def load_universe(self, universe: list[dict[str, str]]) -> None:
        """Load the stock universe (list of dicts with symbol, token, exchange)."""
        self._universe = universe

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scan(self, use_cache: bool = False, cache_dir: str = "data/historical") -> list[dict[str, Any]]:
        """Run the pre-market scan and return a list of candidate stocks.

        Returns list of dicts: {symbol, token, exchange, atr_percent, avg_volume, close}
        """
        if not self._universe:
            logger.warning("Scanner: universe is empty.")
            return []

        results: list[dict[str, Any]] = []
        min_volume: int = self._cfg.get("min_volume", 500000)
        price_min: float = self._cfg.get("price_range", [100, 5000])[0]
        price_max: float = self._cfg.get("price_range", [100, 5000])[1]
        min_atr_pct: float = self._cfg.get("min_atr_percent", 1.5)
        min_turnover_cr: float = self._cfg.get("min_turnover_cr", 10)
        max_stocks: int = self._cfg.get("max_stocks", 15)

        to_dt = datetime.now()
        from_dt = to_dt - timedelta(days=30)

        for stock in self._universe:
            symbol = stock["symbol"]
            token = stock.get("token", "")
            exchange = stock.get("exchange", "NSE")

            try:
                df = await self._fetch_data(symbol, token, exchange, from_dt, to_dt, use_cache, cache_dir)
                if df is None or len(df) < 20:
                    continue

                # Price filter
                last_close = float(df["close"].iloc[-1])
                if not (price_min <= last_close <= price_max):
                    continue

                # Volume filter
                avg_vol = float(df["volume"].tail(20).mean())
                if avg_vol < min_volume:
                    continue

                # Turnover filter (in crores)
                avg_turnover_cr = avg_vol * last_close / 1e7
                if avg_turnover_cr < min_turnover_cr:
                    continue

                # ATR filter
                atr = self._calculate_atr(df, period=14)
                atr_pct = atr / last_close * 100
                if atr_pct < min_atr_pct:
                    continue

                results.append({
                    "symbol": symbol,
                    "token": token,
                    "exchange": exchange,
                    "close": round(last_close, 2),
                    "avg_volume": int(avg_vol),
                    "atr": round(atr, 2),
                    "atr_percent": round(atr_pct, 2),
                    "avg_turnover_cr": round(avg_turnover_cr, 2),
                })
            except Exception as exc:
                logger.error(f"Scanner: error processing {symbol}: {exc}")

        # Sort by ATR% descending and return top N
        results.sort(key=lambda x: x["atr_percent"], reverse=True)
        selected = results[:max_stocks]
        logger.info(f"Scanner: found {len(results)} candidates, selected top {len(selected)}.")
        return selected

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_data(
        self,
        symbol: str,
        token: str,
        exchange: str,
        from_dt: datetime,
        to_dt: datetime,
        use_cache: bool,
        cache_dir: str,
    ) -> pd.DataFrame | None:
        """Load OHLCV data from cache or broker."""
        import os

        cache_path = os.path.join(cache_dir, f"{symbol}_1D.parquet")

        if use_cache and os.path.exists(cache_path):
            try:
                df = pd.read_parquet(cache_path)
                logger.debug(f"Scanner: loaded {symbol} from cache.")
                return df
            except Exception as exc:
                logger.warning(f"Scanner: cache read failed for {symbol}: {exc}")

        if self._broker is None:
            logger.debug(f"Scanner: no broker, skipping {symbol}.")
            return None

        df = await self._broker.get_historical(symbol, token, "ONE_DAY", from_dt, to_dt, exchange)
        if not df.empty and not use_cache:
            try:
                os.makedirs(cache_dir, exist_ok=True)
                df.to_parquet(cache_path, index=False)
            except Exception:
                pass
        return df if not df.empty else None

    @staticmethod
    def _calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
        """Calculate Average True Range over *period* candles."""
        high = df["high"].values
        low = df["low"].values
        close = df["close"].values

        tr = np.maximum(
            high[1:] - low[1:],
            np.maximum(
                np.abs(high[1:] - close[:-1]),
                np.abs(low[1:] - close[:-1]),
            ),
        )
        if len(tr) < period:
            return float(np.mean(tr)) if len(tr) > 0 else 0.0

        # Wilder's smoothed ATR
        atr = float(np.mean(tr[:period]))
        for i in range(period, len(tr)):
            atr = (atr * (period - 1) + tr[i]) / period
        return atr

    @staticmethod
    def filter_by_sector(
        stocks: list[dict[str, Any]], exclude_sectors: list[str]
    ) -> list[dict[str, Any]]:
        """Remove stocks belonging to excluded sectors."""
        if not exclude_sectors:
            return stocks
        return [s for s in stocks if s.get("sector", "") not in exclude_sectors]
