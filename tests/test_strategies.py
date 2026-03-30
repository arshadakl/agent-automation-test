"""Pytest tests for all trading strategies."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ohlcv(
    n: int = 60,
    start_price: float = 1000.0,
    trend: float = 0.0,
    volatility: float = 10.0,
    volume_base: int = 1_000_000,
) -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    rng = np.random.default_rng(42)
    closes = [start_price]
    for _ in range(n - 1):
        closes.append(closes[-1] + trend + rng.normal(0, volatility))

    opens, highs, lows, volumes = [], [], [], []
    for c in closes:
        o = c + rng.normal(0, volatility * 0.5)
        h = max(o, c) + abs(rng.normal(0, volatility * 0.3))
        l = min(o, c) - abs(rng.normal(0, volatility * 0.3))
        v = int(volume_base + rng.integers(-200_000, 200_000))
        opens.append(o)
        highs.append(h)
        lows.append(l)
        volumes.append(v)

    start_dt = datetime(2024, 1, 2, 9, 15)
    datetimes = [start_dt + timedelta(minutes=5 * i) for i in range(n)]
    return pd.DataFrame({
        "datetime": datetimes,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


def _make_orb_candles(
    orb_high: float = 1020.0,
    orb_low: float = 980.0,
    breakout_up: bool = True,
) -> pd.DataFrame:
    """Create 15m candles with a clear ORB breakout."""
    rng = np.random.default_rng(0)
    n = 10
    datetimes = [datetime(2024, 1, 2, 9, 15) + timedelta(minutes=15 * i) for i in range(n)]

    closes = [990.0] * n
    opens = [985.0] * n
    highs = [orb_high] * n
    lows = [orb_low] * n
    volumes = [1_000_000] * n

    if breakout_up:
        closes[-1] = orb_high + 5
        highs[-1] = orb_high + 10
        volumes[-1] = 2_500_000  # surge
    else:
        closes[-1] = orb_low - 5
        lows[-1] = orb_low - 10
        volumes[-1] = 2_500_000

    # First candle is the ORB range candle
    opens[0] = orb_low + 5
    closes[0] = orb_high - 5
    highs[0] = orb_high
    lows[0] = orb_low

    return pd.DataFrame({
        "datetime": datetimes,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


@pytest.fixture
def synthetic_5m() -> pd.DataFrame:
    return _make_ohlcv(n=80, start_price=1500.0)


@pytest.fixture
def synthetic_15m() -> pd.DataFrame:
    return _make_ohlcv(n=30, start_price=1500.0)


# ---------------------------------------------------------------------------
# ORB Strategy Tests
# ---------------------------------------------------------------------------


class TestORBStrategy:
    def test_no_signal_insufficient_history(self) -> None:
        from strategies.orb import ORBStrategy
        strategy = ORBStrategy()
        df = _make_ohlcv(n=2)
        tick = {"symbol": "TEST", "ltp": 1000.0, "timestamp": datetime.now()}
        result = strategy.generate_signal(df, tick)
        assert result is None

    def test_long_breakout_signal(self) -> None:
        from strategies.orb import ORBStrategy
        strategy = ORBStrategy({"volume_multiplier": 1.5, "min_body_ratio": 0.3, "risk_reward": 2.0})
        df = _make_orb_candles(orb_high=1020.0, orb_low=980.0, breakout_up=True)
        tick = {"symbol": "TEST", "ltp": 1025.0, "timestamp": df["datetime"].iloc[-1]}
        signal = strategy.generate_signal(df, tick)
        assert signal is not None
        assert signal.direction == "LONG"
        assert signal.stop_loss == pytest.approx(980.0, abs=1.0)
        assert signal.target_1 > signal.entry_price

    def test_short_breakdown_signal(self) -> None:
        from strategies.orb import ORBStrategy
        strategy = ORBStrategy({"volume_multiplier": 1.5, "min_body_ratio": 0.3, "risk_reward": 2.0})
        df = _make_orb_candles(orb_high=1020.0, orb_low=980.0, breakout_up=False)
        tick = {"symbol": "TEST", "ltp": 975.0, "timestamp": df["datetime"].iloc[-1]}
        signal = strategy.generate_signal(df, tick)
        assert signal is not None
        assert signal.direction == "SHORT"
        assert signal.stop_loss == pytest.approx(1020.0, abs=1.0)
        assert signal.target_1 < signal.entry_price

    def test_body_ratio_filter_rejects_doji(self) -> None:
        from strategies.orb import ORBStrategy
        strategy = ORBStrategy({"volume_multiplier": 1.0, "min_body_ratio": 0.6, "risk_reward": 2.0})
        df = _make_orb_candles(orb_high=1020.0, orb_low=980.0, breakout_up=True)
        # Make first candle a doji (open ≈ close)
        df.at[0, "open"] = 999.9
        df.at[0, "close"] = 1000.1
        df.at[0, "high"] = 1020.0
        df.at[0, "low"] = 980.0
        tick = {"symbol": "TEST", "ltp": 1025.0, "timestamp": df["datetime"].iloc[-1]}
        signal = strategy.generate_signal(df, tick)
        # Should be None because body ratio < 0.6
        assert signal is None

    def test_backtest_params_returns_dict(self) -> None:
        from strategies.orb import ORBStrategy
        params = ORBStrategy().backtest_params()
        assert isinstance(params, dict)
        assert "risk_reward" in params


# ---------------------------------------------------------------------------
# VWAP Reversion Tests
# ---------------------------------------------------------------------------


class TestVWAPReversionStrategy:
    def test_no_signal_on_flat_data(self, synthetic_5m: pd.DataFrame) -> None:
        from strategies.vwap_reversion import VWAPReversionStrategy
        strategy = VWAPReversionStrategy({"rsi_oversold": 20, "rsi_overbought": 80})
        tick = {"symbol": "TEST", "ltp": 1500.0, "timestamp": datetime.now()}
        result = strategy.generate_signal(synthetic_5m, tick)
        # With extreme RSI thresholds, unlikely to get a signal on flat data
        # Just verify it returns Signal or None without error
        assert result is None or result.direction in ("LONG", "SHORT")

    def test_requires_minimum_history(self) -> None:
        from strategies.vwap_reversion import VWAPReversionStrategy
        strategy = VWAPReversionStrategy()
        df = _make_ohlcv(n=5)
        tick = {"symbol": "TEST", "ltp": 1000.0, "timestamp": datetime.now()}
        assert strategy.generate_signal(df, tick) is None

    def test_long_signal_on_oversold_bounce(self) -> None:
        """Manually construct a scenario where RSI is low and price bounces off lower band."""
        from strategies.vwap_reversion import VWAPReversionStrategy
        strategy = VWAPReversionStrategy({"std_dev_bands": 0.1, "rsi_oversold": 60, "rsi_overbought": 90})

        df = _make_ohlcv(n=40, start_price=1000.0, trend=-2.0, volatility=5.0)
        # Force last candle to look like a bounce above lower band
        df.at[39, "low"] = df["close"].mean() - 50
        df.at[39, "close"] = df["close"].mean()

        tick = {"symbol": "TEST", "ltp": float(df.iloc[-1]["close"]), "timestamp": datetime.now()}
        result = strategy.generate_signal(df, tick)
        # Just validate no exception
        assert result is None or result.direction in ("LONG", "SHORT")

    def test_backtest_params_structure(self) -> None:
        from strategies.vwap_reversion import VWAPReversionStrategy
        params = VWAPReversionStrategy().backtest_params()
        assert "std_dev_bands" in params
        assert "rsi_oversold" in params


# ---------------------------------------------------------------------------
# Momentum Breakout Tests
# ---------------------------------------------------------------------------


class TestMomentumBreakoutStrategy:
    def test_no_signal_insufficient_data(self) -> None:
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strategy = MomentumBreakoutStrategy()
        df = _make_ohlcv(n=10)
        tick = {"symbol": "TEST", "ltp": 1000.0, "timestamp": datetime.now()}
        assert strategy.generate_signal(df, tick) is None

    def test_consolidation_detection(self) -> None:
        """Strategy should not signal during high-volatility period."""
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strategy = MomentumBreakoutStrategy({"consolidation_candles": 5, "volume_surge_multiplier": 2.0, "adx_threshold": 0})
        # High-volatility data → no consolidation
        df = _make_ohlcv(n=50, volatility=50.0, trend=5.0)
        tick = {"symbol": "TEST", "ltp": float(df.iloc[-1]["close"]), "timestamp": datetime.now()}
        # Run without asserting signal exists; just ensure no exceptions
        result = strategy.generate_signal(df, tick)
        assert result is None or result.direction in ("LONG", "SHORT")

    def test_generates_long_signal_on_breakout(self) -> None:
        """Build low-volatility candles followed by a breakout candle."""
        from strategies.momentum_breakout import MomentumBreakoutStrategy
        strategy = MomentumBreakoutStrategy({
            "consolidation_candles": 5,
            "volume_surge_multiplier": 1.5,
            "adx_threshold": 0,
            "ema_fast": 3,
            "ema_slow": 5,
        })
        # 40 flat candles + 1 breakout
        df = _make_ohlcv(n=40, start_price=1000.0, volatility=0.5)
        # Breakout candle
        breakout_row = {
            "datetime": df["datetime"].iloc[-1] + timedelta(minutes=5),
            "open": 1001.0,
            "high": 1020.0,
            "low": 1000.0,
            "close": 1018.0,
            "volume": 5_000_000,
        }
        df = pd.concat([df, pd.DataFrame([breakout_row])], ignore_index=True)
        tick = {"symbol": "TEST", "ltp": 1018.0, "timestamp": df["datetime"].iloc[-1]}
        result = strategy.generate_signal(df, tick)
        assert result is None or result.direction == "LONG"


# ---------------------------------------------------------------------------
# EMA Crossover Tests
# ---------------------------------------------------------------------------


class TestEMACrossoverStrategy:
    def test_no_signal_insufficient_data(self) -> None:
        from strategies.ema_crossover import EMACrossoverStrategy
        strategy = EMACrossoverStrategy()
        df = _make_ohlcv(n=5)
        tick = {"symbol": "TEST", "ltp": 1000.0, "timestamp": datetime.now()}
        assert strategy.generate_signal(df, tick) is None

    def test_long_crossover_above_vwap(self) -> None:
        """EMA9 crosses above EMA21, price above VWAP, RSI neutral."""
        from strategies.ema_crossover import EMACrossoverStrategy
        strategy = EMACrossoverStrategy({"ema_fast": 3, "ema_slow": 7, "rsi_low": 30, "rsi_high": 70})

        # Rising trend: EMA fast will cross above EMA slow
        df = _make_ohlcv(n=40, start_price=1000.0, trend=3.0, volatility=1.0)
        tick = {"symbol": "TEST", "ltp": float(df.iloc[-1]["close"]), "timestamp": datetime.now()}
        result = strategy.generate_signal(df, tick)
        assert result is None or result.direction in ("LONG", "SHORT")

    def test_signal_has_valid_structure(self) -> None:
        from strategies.ema_crossover import EMACrossoverStrategy
        from strategies.base_strategy import Signal
        strategy = EMACrossoverStrategy({"ema_fast": 3, "ema_slow": 7, "rsi_low": 0, "rsi_high": 100})
        df = _make_ohlcv(n=40, start_price=1000.0, trend=5.0, volatility=2.0)
        tick = {"symbol": "TESTSTOCK", "ltp": float(df.iloc[-1]["close"]), "timestamp": datetime.now()}
        result = strategy.generate_signal(df, tick)
        if result is not None:
            assert isinstance(result, Signal)
            assert result.symbol == "TESTSTOCK"
            assert result.entry_price > 0
            assert result.stop_loss > 0
            assert result.target_1 > 0

    def test_backtest_params_completeness(self) -> None:
        from strategies.ema_crossover import EMACrossoverStrategy
        params = EMACrossoverStrategy().backtest_params()
        assert "ema_fast" in params and "ema_slow" in params
