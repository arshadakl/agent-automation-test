"""VWAP Reversion strategy – 5-minute timeframe."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
from loguru import logger

from strategies.base_strategy import BaseStrategy, Signal


class VWAPReversionStrategy(BaseStrategy):
    """VWAP Reversion (mean-reversion).

    - Timeframe: 5m
    - LONG: price touches VWAP − 1 std-dev band AND RSI < 35 AND next candle
      closes above the band → buy the bounce.
    - SHORT: price touches VWAP + 1 std-dev band AND RSI > 65 AND next candle
      closes below the band → sell the reversal.
    - Stop loss: 0.5% beyond entry.
    - Target: VWAP (centre line).
    """

    name = "vwap_reversion"
    timeframe = "5m"
    required_history = 20

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self._std_dev_bands: float = self.params.get("std_dev_bands", 1.0)
        self._rsi_period: int = int(self.params.get("rsi_period", 14))
        self._rsi_oversold: float = self.params.get("rsi_oversold", 35.0)
        self._rsi_overbought: float = self.params.get("rsi_overbought", 65.0)
        self._sl_pct: float = 0.005  # 0.5% stop loss

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if len(candles) < self.required_history:
            return None

        symbol: str = tick.get("symbol", "")
        ts = tick.get("timestamp") or datetime.now()
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)

        df = candles.copy().reset_index(drop=True)

        vwap = self._vwap(df)
        rsi = self._rsi(df["close"], self._rsi_period)

        # VWAP standard deviation band
        typical = (df["high"] + df["low"] + df["close"]) / 3
        std = typical.rolling(window=20).std()
        upper_band = vwap + self._std_dev_bands * std
        lower_band = vwap - self._std_dev_bands * std

        last = df.iloc[-1]
        prev = df.iloc[-2]
        close = float(last["close"])
        low = float(last["low"])
        high = float(last["high"])
        vwap_val = float(vwap.iloc[-1])
        lower = float(lower_band.iloc[-1])
        upper = float(upper_band.iloc[-1])
        rsi_val = float(rsi.iloc[-1])

        if pd.isna(vwap_val) or pd.isna(lower) or pd.isna(upper):
            return None

        # LONG: previous candle touched/pierced lower band; this candle bounces
        if (
            float(prev["low"]) <= lower
            and close > lower
            and rsi_val < self._rsi_oversold
        ):
            sl = round(close * (1 - self._sl_pct), 2)
            target = round(vwap_val, 2)
            if target <= close:
                return None
            return Signal(
                strategy=self.name,
                symbol=symbol,
                direction="LONG",
                entry_price=round(close, 2),
                stop_loss=sl,
                target_1=target,
                target_2=None,
                confidence=round(max(0.0, (self._rsi_oversold - rsi_val) / self._rsi_oversold), 2),
                timestamp=ts,
                metadata={"vwap": vwap_val, "lower_band": lower, "upper_band": upper},
            )

        # SHORT: previous candle touched/pierced upper band; this candle reverses
        if (
            float(prev["high"]) >= upper
            and close < upper
            and rsi_val > self._rsi_overbought
        ):
            sl = round(close * (1 + self._sl_pct), 2)
            target = round(vwap_val, 2)
            if target >= close:
                return None
            return Signal(
                strategy=self.name,
                symbol=symbol,
                direction="SHORT",
                entry_price=round(close, 2),
                stop_loss=sl,
                target_1=target,
                target_2=None,
                confidence=round(max(0.0, (rsi_val - self._rsi_overbought) / (100 - self._rsi_overbought)), 2),
                timestamp=ts,
                metadata={"vwap": vwap_val, "lower_band": lower, "upper_band": upper},
            )

        return None

    def get_stop_loss(self, entry_price: float, signal: Signal) -> float:
        return signal.stop_loss

    def get_targets(self, entry_price: float, signal: Signal) -> list[float]:
        return [signal.target_1]

    def backtest_params(self) -> dict:
        return {
            "std_dev_bands": [0.75, 1.0, 1.25, 1.5],
            "rsi_oversold": [30, 35, 40],
            "rsi_overbought": [60, 65, 70],
        }
