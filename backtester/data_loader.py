"""Historical data loader – Parquet cache + Angel One API download."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from loguru import logger


class DataLoader:
    """Loads OHLCV data from local Parquet cache or Angel One API.

    Cache naming convention:
        ``{cache_dir}/{symbol}_{interval}.parquet``

    Intervals (Angel One format):
        ONE_MINUTE, FIVE_MINUTE, FIFTEEN_MINUTE, THIRTY_MINUTE,
        ONE_HOUR, ONE_DAY
    """

    INTERVAL_MAP: dict[str, str] = {
        "1m":  "ONE_MINUTE",
        "5m":  "FIVE_MINUTE",
        "15m": "FIFTEEN_MINUTE",
        "30m": "THIRTY_MINUTE",
        "1h":  "ONE_HOUR",
        "1d":  "ONE_DAY",
    }

    def __init__(
        self,
        cache_dir: str = "data/historical",
        broker: Any | None = None,
    ) -> None:
        self._cache_dir = cache_dir
        self._broker = broker
        os.makedirs(cache_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(
        self,
        symbol: str,
        interval: str = "5m",
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        token: str = "",
        exchange: str = "NSE",
        force_download: bool = False,
    ) -> pd.DataFrame:
        """Return an OHLCV DataFrame for *symbol* in *interval*.

        Loads from cache if available and up-to-date; otherwise downloads
        from Angel One and writes to cache.
        """
        to_date = to_date or datetime.now()
        from_date = from_date or (to_date - timedelta(days=180))

        cache_path = self._cache_path(symbol, interval)

        if not force_download and os.path.exists(cache_path):
            df = self._load_parquet(cache_path)
            if df is not None and not df.empty:
                # Check if cache covers the requested range
                last_cached = df["datetime"].max()
                gap_hours = (to_date - last_cached).total_seconds() / 3600
                # If data is fresh enough (< 1 day old for intraday), use it
                if gap_hours < 24 or interval == "1d":
                    logger.debug(f"DataLoader: loaded {symbol} {interval} from cache ({len(df)} rows).")
                    return df[
                        (df["datetime"] >= pd.Timestamp(from_date))
                        & (df["datetime"] <= pd.Timestamp(to_date))
                    ].copy().reset_index(drop=True)

        # Download
        if self._broker is None:
            logger.warning(f"DataLoader: no broker and no cache for {symbol} {interval}.")
            return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])

        import asyncio
        api_interval = self.INTERVAL_MAP.get(interval, "FIVE_MINUTE")
        try:
            loop = asyncio.get_event_loop()
            df = loop.run_until_complete(
                self._broker.get_historical(symbol, token, api_interval, from_date, to_date, exchange)
            )
        except RuntimeError:
            # No running event loop
            df = asyncio.run(
                self._broker.get_historical(symbol, token, api_interval, from_date, to_date, exchange)
            )

        if df is None or df.empty:
            logger.warning(f"DataLoader: empty data returned for {symbol} {interval}.")
            return pd.DataFrame(columns=["datetime", "open", "high", "low", "close", "volume"])

        df = self._clean(df)
        self._save_parquet(df, cache_path)
        logger.info(f"DataLoader: downloaded {len(df)} rows for {symbol} {interval}.")
        return df

    def resample(self, df: pd.DataFrame, target_tf: str) -> pd.DataFrame:
        """Resample an OHLCV DataFrame to *target_tf* (e.g., '5T', '15T')."""
        tf_map = {"1m": "1T", "5m": "5T", "15m": "15T", "30m": "30T", "1h": "1h", "1d": "1D"}
        freq = tf_map.get(target_tf, target_tf)

        if "datetime" not in df.columns:
            return df

        df = df.copy()
        df = df.set_index("datetime")
        df.index = pd.to_datetime(df.index)

        resampled = df.resample(freq).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }).dropna(subset=["close"])

        resampled = resampled.reset_index().rename(columns={"index": "datetime"})
        return resampled

    def list_cached(self) -> list[str]:
        """Return list of cached parquet files."""
        if not os.path.exists(self._cache_dir):
            return []
        return [f for f in os.listdir(self._cache_dir) if f.endswith(".parquet")]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cache_path(self, symbol: str, interval: str) -> str:
        return os.path.join(self._cache_dir, f"{symbol}_{interval}.parquet")

    def _load_parquet(self, path: str) -> pd.DataFrame | None:
        try:
            df = pd.read_parquet(path)
            df["datetime"] = pd.to_datetime(df["datetime"])
            return df
        except Exception as exc:
            logger.warning(f"DataLoader: failed to load parquet {path}: {exc}")
            return None

    def _save_parquet(self, df: pd.DataFrame, path: str) -> None:
        try:
            df.to_parquet(path, index=False)
            logger.debug(f"DataLoader: saved {path}.")
        except Exception as exc:
            logger.error(f"DataLoader: failed to save parquet {path}: {exc}")

    @staticmethod
    def _clean(df: pd.DataFrame) -> pd.DataFrame:
        """Validate and clean OHLCV data."""
        required = {"datetime", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataLoader: missing columns {missing}")

        df = df.copy()
        df["datetime"] = pd.to_datetime(df["datetime"])
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype(int)

        # Drop rows where high < low (data errors)
        df = df[df["high"] >= df["low"]]
        # Drop rows with zero/negative prices
        df = df[(df["close"] > 0) & (df["open"] > 0)]
        df = df.sort_values("datetime").drop_duplicates(subset=["datetime"]).reset_index(drop=True)
        return df
