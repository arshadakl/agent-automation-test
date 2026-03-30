"""Base strategy abstraction and Signal dataclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd


@dataclass
class Signal:
    """A trading signal produced by a strategy."""

    strategy: str
    symbol: str
    direction: str         # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float | None
    confidence: float      # 0.0 – 1.0
    timestamp: datetime
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.direction not in ("LONG", "SHORT"):
            raise ValueError(f"direction must be LONG or SHORT, got {self.direction!r}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be between 0 and 1")


class BaseStrategy(ABC):
    """Abstract base class for all intraday strategies."""

    name: str = "base"
    timeframe: str = "5m"      # "1m", "5m", "15m"
    required_history: int = 50  # minimum candles needed

    def __init__(self, params: dict | None = None) -> None:
        self.params: dict = params or {}

    @abstractmethod
    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        """Analyse *candles* and the latest *tick* and return a Signal or None."""
        ...

    @abstractmethod
    def get_stop_loss(self, entry_price: float, signal: Signal) -> float:
        """Return the stop-loss price for the signal."""
        ...

    @abstractmethod
    def get_targets(self, entry_price: float, signal: Signal) -> list[float]:
        """Return a list of target prices [target_1, target_2, …]."""
        ...

    @abstractmethod
    def backtest_params(self) -> dict:
        """Return default parameter dict for backtesting."""
        ...

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _vwap(df: pd.DataFrame) -> pd.Series:
        """Compute intraday VWAP from a candle DataFrame."""
        typical = (df["high"] + df["low"] + df["close"]) / 3
        cum_tvol = (typical * df["volume"]).cumsum()
        cum_vol = df["volume"].cumsum()
        return cum_tvol / cum_vol.replace(0, float("nan"))

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """True Range and ATR."""
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
        rs = gain / loss.replace(0, float("nan"))
        return 100 - (100 / (1 + rs))
