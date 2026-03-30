"""Backtester package."""

from __future__ import annotations

from backtester.engine import BacktestEngine, BacktestResult
from backtester.data_loader import DataLoader
from backtester.report import BacktestReport
from backtester.optimizer import StrategyOptimizer

__all__ = ["BacktestEngine", "BacktestResult", "DataLoader", "BacktestReport", "StrategyOptimizer"]
