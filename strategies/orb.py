"""Opening Range Breakout (ORB) strategy – 15-minute timeframe."""

from __future__ import annotations

from datetime import datetime, time

import pandas as pd

from loguru import logger
from strategies.base_strategy import BaseStrategy, Signal


class ORBStrategy(BaseStrategy):
    """Opening Range Breakout.

    - Timeframe: 15m
    - The *opening range* is defined by the first completed 15m candle
      (09:15 – 09:30).
    - LONG when price closes above the ORB high with volume > multiplier × avg.
    - SHORT when price closes below the ORB low with volume > multiplier × avg.
    - Body ratio filter: |open-close| / (high-low) ≥ min_body_ratio.
    - Stop: opposite ORB boundary.
    - Target: risk × risk_reward ratio.
    """

    name = "orb"
    timeframe = "15m"
    required_history = 3

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self._volume_multiplier: float = self.params.get("volume_multiplier", 1.5)
        self._min_body_ratio: float = self.params.get("min_body_ratio", 0.6)
        self._risk_reward: float = self.params.get("risk_reward", 2.0)
        # Cache per symbol: {symbol: {"high": float, "low": float}}
        self._orb: dict[str, dict[str, float]] = {}

    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None:
        """Generate ORB signal on candle close."""
        if len(candles) < self.required_history:
            return None

        symbol: str = tick.get("symbol", "")
        ts = tick.get("timestamp") or datetime.now()
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)

        # Identify the opening-range candle (first 15m candle of the day)
        first_candle = self._get_orb_candle(candles)
        if first_candle is None:
            return None

        orb_high = float(first_candle["high"])
        orb_low = float(first_candle["low"])
        orb_body = abs(float(first_candle["close"]) - float(first_candle["open"]))
        orb_range = orb_high - orb_low

        # Body ratio check
        if orb_range > 0 and orb_body / orb_range < self._min_body_ratio:
            logger.debug(f"ORB: low body ratio for {symbol}, skipping.")
            return None

        latest = candles.iloc[-1]
        prev = candles.iloc[-2]
        close = float(latest["close"])
        volume = float(latest["volume"])

        # Average volume of last 20 sessions (or all available)
        avg_vol = float(candles["volume"].iloc[:-1].tail(20).mean())

        # Ensure we are past the opening range
        if len(candles) < 2:
            return None

        # LONG breakout
        if (
            float(prev["close"]) <= orb_high  # previous close below/at ORB
            and close > orb_high
            and volume >= self._volume_multiplier * avg_vol
        ):
            sl = orb_low
            risk = close - sl
            if risk <= 0:
                return None
            target_1 = close + risk * self._risk_reward
            return Signal(
                strategy=self.name,
                symbol=symbol,
                direction="LONG",
                entry_price=round(close, 2),
                stop_loss=round(sl, 2),
                target_1=round(target_1, 2),
                target_2=None,
                confidence=self._calc_confidence(volume, avg_vol),
                timestamp=ts,
            )

        # SHORT breakdown
        if (
            float(prev["close"]) >= orb_low
            and close < orb_low
            and volume >= self._volume_multiplier * avg_vol
        ):
            sl = orb_high
            risk = sl - close
            if risk <= 0:
                return None
            target_1 = close - risk * self._risk_reward
            return Signal(
                strategy=self.name,
                symbol=symbol,
                direction="SHORT",
                entry_price=round(close, 2),
                stop_loss=round(sl, 2),
                target_1=round(target_1, 2),
                target_2=None,
                confidence=self._calc_confidence(volume, avg_vol),
                timestamp=ts,
            )

        return None

    def get_stop_loss(self, entry_price: float, signal: Signal) -> float:
        return signal.stop_loss

    def get_targets(self, entry_price: float, signal: Signal) -> list[float]:
        return [signal.target_1]

    def backtest_params(self) -> dict:
        return {
            "volume_multiplier": [1.2, 1.5, 2.0],
            "min_body_ratio": [0.5, 0.6, 0.7],
            "risk_reward": [1.5, 2.0, 2.5],
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_orb_candle(self, candles: pd.DataFrame) -> pd.Series | None:
        """Return the first 15m candle of the trading day (09:15 candle)."""
        if "datetime" not in candles.columns:
            return candles.iloc[0]
        candles = candles.copy()
        candles["datetime"] = pd.to_datetime(candles["datetime"])
        today = candles["datetime"].iloc[-1].date()
        day_candles = candles[candles["datetime"].dt.date == today]
        if day_candles.empty:
            return None
        return day_candles.iloc[0]

    @staticmethod
    def _calc_confidence(volume: float, avg_vol: float) -> float:
        if avg_vol == 0:
            return 0.5
        ratio = volume / avg_vol
        return min(round(ratio / 3.0, 2), 1.0)
