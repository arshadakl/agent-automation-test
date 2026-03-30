"""Strategies package."""

from __future__ import annotations

from strategies.base_strategy import BaseStrategy, Signal
from strategies.orb import ORBStrategy
from strategies.vwap_reversion import VWAPReversionStrategy
from strategies.momentum_breakout import MomentumBreakoutStrategy
from strategies.ema_crossover import EMACrossoverStrategy
from strategies.gap_fill import GapFillStrategy

__all__ = [
    "BaseStrategy",
    "Signal",
    "ORBStrategy",
    "VWAPReversionStrategy",
    "MomentumBreakoutStrategy",
    "EMACrossoverStrategy",
    "GapFillStrategy",
]
