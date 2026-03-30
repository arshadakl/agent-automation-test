"""Momentum Breakout strategy – 5-minute timeframe."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from loguru import logger

from strategies.base_strategy import BaseStrategy, Signal


class MomentumBreakoutStrategy(BaseStrategy):
    """Intraday consolidation breakout.

    Algorithm:
    1. Detect consolidation: ATR of last *consolidation_candles* < 0.5 × full ATR.
    2. Breakout: close breaks above consolidation high (LONG) or below low (SHORT).
    3. Volume confirmation: current volume > *volume_surge_multiplier* × avg volume.
    4. ADX > *adx_threshold* confirms trend strength.
    5. EMA fast / slow crossover in the direction of the trade.

    Stop loss: consolidation low (long) / high (short).
    Target: entry ± 2 × consolidation range.
    """

    name = "momentum_breakout"
    timeframe = "5m"
    required_history = 30

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self._cons_candles: int = int(self.params.get("consolidation_candles", 10))
        self._vol_surge: float = self.params.get("volume_surge_multiplier", 2.0)
        self._adx_threshold: float = self.params.get("adx_threshold", 25.0)
        self._ema_fast: int = int(self.params.get("ema_fast", 9))
        self._ema_slow: int = int(self.params.get("ema_slow", 21))

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        if len(candles) < max(self.required_history, self._cons_candles + 2):
            return None

        symbol: str = tick.get("symbol", "")
        ts = tick.get("timestamp") or datetime.now()
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)

        df = candles.copy().reset_index(drop=True)

        # ATR
        atr_series = self._atr(df, period=14)
        avg_atr = float(atr_series.iloc[-self._cons_candles - 10 : -self._cons_candles].mean())
        cons_atr = float(atr_series.iloc[-self._cons_candles:].mean())

        if pd.isna(avg_atr) or avg_atr == 0:
            return None

        is_consolidating = cons_atr < 0.5 * avg_atr

        if not is_consolidating:
            return None

        # Consolidation range
        cons_df = df.iloc[-self._cons_candles - 1 : -1]  # exclude latest candle
        cons_high = float(cons_df["high"].max())
        cons_low = float(cons_df["low"].min())
        cons_range = cons_high - cons_low
        if cons_range <= 0:
            return None

        latest = df.iloc[-1]
        close = float(latest["close"])
        volume = float(latest["volume"])
        avg_vol = float(df["volume"].iloc[-30:-1].mean())

        # Volume surge
        if avg_vol == 0 or volume < self._vol_surge * avg_vol:
            return None

        # ADX
        adx_val = self._adx(df, period=14)
        if adx_val < self._adx_threshold:
            return None

        # EMA crossover
        ema_f = self._ema(df["close"], self._ema_fast)
        ema_s = self._ema(df["close"], self._ema_slow)
        ema_fast_now = float(ema_f.iloc[-1])
        ema_slow_now = float(ema_s.iloc[-1])
        ema_fast_prev = float(ema_f.iloc[-2])
        ema_slow_prev = float(ema_s.iloc[-2])

        # LONG breakout
        if close > cons_high and ema_fast_now > ema_slow_now:
            if ema_fast_prev <= ema_slow_prev or ema_fast_now > ema_slow_now:
                sl = round(cons_low, 2)
                target = round(close + 2 * cons_range, 2)
                confidence = min(round(volume / (avg_vol * self._vol_surge), 2), 1.0)
                return Signal(
                    strategy=self.name,
                    symbol=symbol,
                    direction="LONG",
                    entry_price=round(close, 2),
                    stop_loss=sl,
                    target_1=target,
                    target_2=None,
                    confidence=confidence,
                    timestamp=ts,
                    metadata={"cons_high": cons_high, "cons_low": cons_low, "adx": adx_val},
                )

        # SHORT breakdown
        if close < cons_low and ema_fast_now < ema_slow_now:
            sl = round(cons_high, 2)
            target = round(close - 2 * cons_range, 2)
            confidence = min(round(volume / (avg_vol * self._vol_surge), 2), 1.0)
            return Signal(
                strategy=self.name,
                symbol=symbol,
                direction="SHORT",
                entry_price=round(close, 2),
                stop_loss=sl,
                target_1=target,
                target_2=None,
                confidence=confidence,
                timestamp=ts,
                metadata={"cons_high": cons_high, "cons_low": cons_low, "adx": adx_val},
            )

        return None

    def get_stop_loss(self, entry_price: float, signal: Signal) -> float:
        return signal.stop_loss

    def get_targets(self, entry_price: float, signal: Signal) -> list[float]:
        return [signal.target_1]

    def backtest_params(self) -> dict:
        return {
            "consolidation_candles": [8, 10, 12],
            "volume_surge_multiplier": [1.5, 2.0, 2.5],
            "adx_threshold": [20, 25, 30],
        }

    # ------------------------------------------------------------------
    # ADX helper
    # ------------------------------------------------------------------

    @staticmethod
    def _adx(df: pd.DataFrame, period: int = 14) -> float:
        """Approximate ADX value at the last row."""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        plus_dm = (high.diff()).clip(lower=0)
        minus_dm = (-low.diff()).clip(lower=0)
        # Where +DM > -DM keep +DM else 0
        plus_dm = plus_dm.where(plus_dm > minus_dm, 0.0)
        minus_dm = minus_dm.where(minus_dm > plus_dm, 0.0)

        prev_close = close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)

        atr_s = tr.ewm(span=period, adjust=False).mean()
        plus_di = 100 * plus_dm.ewm(span=period, adjust=False).mean() / atr_s.replace(0, float("nan"))
        minus_di = 100 * minus_dm.ewm(span=period, adjust=False).mean() / atr_s.replace(0, float("nan"))
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
        adx = dx.ewm(span=period, adjust=False).mean()
        val = adx.iloc[-1]
        return float(val) if not pd.isna(val) else 0.0
