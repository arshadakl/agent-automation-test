"""EMA 9/21 Crossover strategy – 5-minute timeframe."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from loguru import logger

from strategies.base_strategy import BaseStrategy, Signal


class EMACrossoverStrategy(BaseStrategy):
    """EMA 9 / 21 crossover with VWAP and RSI filter.

    - Timeframe: 5m
    - LONG: EMA9 crosses above EMA21, price above VWAP, RSI between 40–60.
    - SHORT: EMA9 crosses below EMA21, price below VWAP, RSI between 40–60.
    - Stop: EMA21 value at entry.
    - Target: entry + 1.5 × risk (long) / entry − 1.5 × risk (short).
    """

    name = "ema_crossover"
    timeframe = "5m"
    required_history = 30

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self._fast: int = int(self.params.get("ema_fast", 9))
        self._slow: int = int(self.params.get("ema_slow", 21))
        self._rsi_period: int = int(self.params.get("rsi_period", 14))
        self._rsi_low: float = self.params.get("rsi_low", 40.0)
        self._rsi_high: float = self.params.get("rsi_high", 60.0)
        self._risk_reward: float = self.params.get("risk_reward", 1.5)

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if len(candles) < self.required_history:
            return None

        symbol: str = tick.get("symbol", "")
        ts = tick.get("timestamp") or datetime.now()
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)

        df = candles.copy().reset_index(drop=True)

        ema_f = self._ema(df["close"], self._fast)
        ema_s = self._ema(df["close"], self._slow)
        vwap = self._vwap(df)
        rsi = self._rsi(df["close"], self._rsi_period)

        ema_f_now = float(ema_f.iloc[-1])
        ema_s_now = float(ema_s.iloc[-1])
        ema_f_prev = float(ema_f.iloc[-2])
        ema_s_prev = float(ema_s.iloc[-2])
        vwap_val = float(vwap.iloc[-1])
        rsi_val = float(rsi.iloc[-1])
        close = float(df.iloc[-1]["close"])

        if any(pd.isna(v) for v in [ema_f_now, ema_s_now, vwap_val, rsi_val]):
            return None

        rsi_neutral = self._rsi_low <= rsi_val <= self._rsi_high

        # LONG crossover
        if (
            ema_f_prev <= ema_s_prev
            and ema_f_now > ema_s_now
            and close > vwap_val
            and rsi_neutral
        ):
            sl = round(ema_s_now, 2)
            risk = close - sl
            if risk <= 0:
                return None
            target = round(close + self._risk_reward * risk, 2)
            return Signal(
                strategy=self.name,
                symbol=symbol,
                direction="LONG",
                entry_price=round(close, 2),
                stop_loss=sl,
                target_1=target,
                target_2=None,
                confidence=round(min((ema_f_now - ema_s_now) / ema_s_now * 100, 1.0), 2),
                timestamp=ts,
                metadata={"ema_fast": ema_f_now, "ema_slow": ema_s_now, "vwap": vwap_val, "rsi": rsi_val},
            )

        # SHORT crossover
        if (
            ema_f_prev >= ema_s_prev
            and ema_f_now < ema_s_now
            and close < vwap_val
            and rsi_neutral
        ):
            sl = round(ema_s_now, 2)
            risk = sl - close
            if risk <= 0:
                return None
            target = round(close - self._risk_reward * risk, 2)
            return Signal(
                strategy=self.name,
                symbol=symbol,
                direction="SHORT",
                entry_price=round(close, 2),
                stop_loss=sl,
                target_1=target,
                target_2=None,
                confidence=round(min((ema_s_now - ema_f_now) / ema_s_now * 100, 1.0), 2),
                timestamp=ts,
                metadata={"ema_fast": ema_f_now, "ema_slow": ema_s_now, "vwap": vwap_val, "rsi": rsi_val},
            )

        return None

    def get_stop_loss(self, entry_price: float, signal: Signal) -> float:
        return signal.stop_loss

    def get_targets(self, entry_price: float, signal: Signal) -> list[float]:
        return [signal.target_1]

    def backtest_params(self) -> dict:
        return {
            "ema_fast": [5, 9, 13],
            "ema_slow": [18, 21, 26],
            "rsi_low": [35, 40, 45],
            "rsi_high": [55, 60, 65],
        }
