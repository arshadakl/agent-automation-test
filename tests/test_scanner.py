"""Pytest tests for the pre-market Scanner."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_daily_ohlcv(n: int = 30, close: float = 1000.0, volume: int = 1_000_000) -> pd.DataFrame:
    """Generate *n* daily OHLCV rows."""
    rng = np.random.default_rng(99)
    closes = [close + rng.normal(0, 10) for _ in range(n)]
    opens = [c + rng.normal(0, 5) for c in closes]
    highs = [max(o, c) + abs(rng.normal(0, 3)) for o, c in zip(opens, closes)]
    lows = [min(o, c) - abs(rng.normal(0, 3)) for o, c in zip(opens, closes)]
    volumes = [volume + int(rng.integers(-100_000, 100_000)) for _ in range(n)]
    dts = [datetime(2024, 1, 2) + timedelta(days=i) for i in range(n)]
    return pd.DataFrame({
        "datetime": dts,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


@pytest.fixture
def scanner_config() -> dict:
    return {
        "min_volume": 500_000,
        "min_turnover_cr": 5,
        "price_range": [100, 5000],
        "min_atr_percent": 1.0,
        "max_stocks": 5,
    }


@pytest.fixture
def sample_universe() -> list[dict]:
    return [
        {"symbol": "RELIANCE", "token": "2885", "exchange": "NSE"},
        {"symbol": "TCS", "token": "11536", "exchange": "NSE"},
        {"symbol": "INFY", "token": "1594", "exchange": "NSE"},
        {"symbol": "HDFCBANK", "token": "1333", "exchange": "NSE"},
        {"symbol": "ICICIBANK", "token": "4963", "exchange": "NSE"},
        {"symbol": "SBIN", "token": "3045", "exchange": "NSE"},
    ]


# ---------------------------------------------------------------------------
# ATR Calculation Tests
# ---------------------------------------------------------------------------


class TestATRCalculation:
    def test_atr_positive(self) -> None:
        from core.scanner import Scanner
        df = _make_daily_ohlcv(n=20, close=1000.0)
        atr = Scanner._calculate_atr(df, period=14)
        assert atr > 0

    def test_atr_increases_with_volatility(self) -> None:
        from core.scanner import Scanner
        df_low = _make_daily_ohlcv(n=20, close=1000.0, volume=1_000_000)
        # Make high-volatility data manually
        df_high = df_low.copy()
        df_high["high"] = df_high["close"] + 50
        df_high["low"] = df_high["close"] - 50
        atr_low = Scanner._calculate_atr(df_low)
        atr_high = Scanner._calculate_atr(df_high)
        assert atr_high > atr_low

    def test_atr_with_insufficient_data(self) -> None:
        from core.scanner import Scanner
        df = _make_daily_ohlcv(n=3, close=500.0)
        atr = Scanner._calculate_atr(df, period=14)
        assert atr >= 0  # Should not raise

    def test_atr_percent_calculation(self) -> None:
        from core.scanner import Scanner
        df = _make_daily_ohlcv(n=20, close=1000.0)
        df["high"] = df["close"] + 20   # ~2% daily range
        df["low"] = df["close"] - 20
        atr = Scanner._calculate_atr(df, period=14)
        atr_pct = atr / 1000.0 * 100
        assert 1.0 <= atr_pct <= 10.0


# ---------------------------------------------------------------------------
# Volume Filter Tests
# ---------------------------------------------------------------------------


class TestVolumeFilter:
    def test_low_volume_stock_excluded(self, scanner_config: dict) -> None:
        """Stocks with avg volume < min_volume should be excluded."""
        from core.scanner import Scanner
        scanner = Scanner(scanner_config, broker=None)
        # Only 1 stock in universe with very low volume
        scanner.load_universe([{"symbol": "LOWVOL", "token": "999", "exchange": "NSE"}])

        low_vol_df = _make_daily_ohlcv(n=25, close=500.0, volume=10_000)

        async def _run() -> list:
            with patch.object(scanner, "_fetch_data", new=AsyncMock(return_value=low_vol_df)):
                return await scanner.scan()

        result = asyncio.run(_run())
        assert len(result) == 0

    def test_high_volume_stock_included(self, scanner_config: dict) -> None:
        """Stocks meeting all criteria should appear in results."""
        from core.scanner import Scanner
        scanner = Scanner(scanner_config, broker=None)
        scanner.load_universe([{"symbol": "HIGHVOL", "token": "001", "exchange": "NSE"}])

        good_df = _make_daily_ohlcv(n=25, close=800.0, volume=1_500_000)
        # Ensure high ATR
        good_df["high"] = good_df["close"] * 1.03
        good_df["low"] = good_df["close"] * 0.97

        async def _run() -> list:
            with patch.object(scanner, "_fetch_data", new=AsyncMock(return_value=good_df)):
                return await scanner.scan()

        result = asyncio.run(_run())
        assert len(result) == 1
        assert result[0]["symbol"] == "HIGHVOL"


# ---------------------------------------------------------------------------
# Price Range Filter Tests
# ---------------------------------------------------------------------------


class TestPriceRangeFilter:
    def test_too_cheap_excluded(self, scanner_config: dict) -> None:
        from core.scanner import Scanner
        scanner = Scanner(scanner_config, broker=None)
        scanner.load_universe([{"symbol": "CHEAP", "token": "002", "exchange": "NSE"}])

        cheap_df = _make_daily_ohlcv(n=25, close=50.0, volume=2_000_000)

        async def _run() -> list:
            with patch.object(scanner, "_fetch_data", new=AsyncMock(return_value=cheap_df)):
                return await scanner.scan()

        result = asyncio.run(_run())
        assert len(result) == 0

    def test_too_expensive_excluded(self, scanner_config: dict) -> None:
        from core.scanner import Scanner
        scanner = Scanner(scanner_config, broker=None)
        scanner.load_universe([{"symbol": "PRICEY", "token": "003", "exchange": "NSE"}])

        pricey_df = _make_daily_ohlcv(n=25, close=8000.0, volume=2_000_000)

        async def _run() -> list:
            with patch.object(scanner, "_fetch_data", new=AsyncMock(return_value=pricey_df)):
                return await scanner.scan()

        result = asyncio.run(_run())
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Scan Limit and Sorting Tests
# ---------------------------------------------------------------------------


class TestScanSorting:
    def test_results_sorted_by_atr(self, scanner_config: dict) -> None:
        """Results should be sorted by ATR% descending."""
        from core.scanner import Scanner
        scanner = Scanner({**scanner_config, "max_stocks": 10}, broker=None)
        universe = [
            {"symbol": "HIGH_ATR", "token": "10", "exchange": "NSE"},
            {"symbol": "LOW_ATR", "token": "11", "exchange": "NSE"},
        ]
        scanner.load_universe(universe)

        high_atr_df = _make_daily_ohlcv(n=25, close=500.0, volume=1_500_000)
        high_atr_df["high"] = high_atr_df["close"] * 1.05
        high_atr_df["low"] = high_atr_df["close"] * 0.95

        low_atr_df = _make_daily_ohlcv(n=25, close=500.0, volume=1_500_000)
        low_atr_df["high"] = low_atr_df["close"] * 1.01
        low_atr_df["low"] = low_atr_df["close"] * 0.99

        side_effects = [high_atr_df, low_atr_df]

        async def _run() -> list:
            with patch.object(scanner, "_fetch_data", new=AsyncMock(side_effect=side_effects)):
                return await scanner.scan()

        result = asyncio.run(_run())
        if len(result) >= 2:
            assert result[0]["atr_percent"] >= result[1]["atr_percent"]

    def test_max_stocks_limit(self, scanner_config: dict) -> None:
        """No more than max_stocks results should be returned."""
        from core.scanner import Scanner
        config = {**scanner_config, "max_stocks": 2}
        scanner = Scanner(config, broker=None)
        universe = [{"symbol": f"STOCK{i}", "token": str(i), "exchange": "NSE"} for i in range(6)]
        scanner.load_universe(universe)

        good_df = _make_daily_ohlcv(n=25, close=500.0, volume=1_500_000)
        good_df["high"] = good_df["close"] * 1.03
        good_df["low"] = good_df["close"] * 0.97

        async def _run() -> list:
            with patch.object(scanner, "_fetch_data", new=AsyncMock(return_value=good_df)):
                return await scanner.scan()

        result = asyncio.run(_run())
        assert len(result) <= 2

    def test_empty_universe_returns_empty(self, scanner_config: dict) -> None:
        from core.scanner import Scanner
        scanner = Scanner(scanner_config, broker=None)
        scanner.load_universe([])

        async def _run() -> list:
            return await scanner.scan()

        result = asyncio.run(_run())
        assert result == []
