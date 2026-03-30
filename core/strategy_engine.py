"""Strategy engine – orchestrates all strategies across symbols."""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pandas as pd
from loguru import logger

from strategies.base_strategy import Signal


class StrategyEngine:
    """Loads enabled strategies and dispatches candle updates to each.

    For every closed candle on a relevant timeframe, all strategies that
    use that timeframe are evaluated.  Validated signals are placed in
    ``signal_queue`` for the execution engine to consume.
    """

    STRATEGY_MAP: dict[str, str] = {
        "orb": "strategies.orb.ORBStrategy",
        "vwap_reversion": "strategies.vwap_reversion.VWAPReversionStrategy",
        "momentum_breakout": "strategies.momentum_breakout.MomentumBreakoutStrategy",
        "ema_crossover": "strategies.ema_crossover.EMACrossoverStrategy",
        "gap_fill": "strategies.gap_fill.GapFillStrategy",
    }

    def __init__(
        self,
        config: dict[str, Any],
        risk_manager: "RiskManager",  # noqa: F821
        portfolio: "Portfolio",  # noqa: F821
        data_feed: "DataFeed",  # noqa: F821
    ) -> None:
        self._cfg = config
        self._risk = risk_manager
        self._portfolio = portfolio
        self._data_feed = data_feed
        self._strategies: list[Any] = []
        self.signal_queue: asyncio.Queue[Signal] = asyncio.Queue()
        self._symbols: list[str] = []
        self._running = False

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def load_strategies(self) -> None:
        """Instantiate all enabled strategies from config."""
        enabled: list[str] = self._cfg.get("enabled", [])
        for name in enabled:
            cls_path = self.STRATEGY_MAP.get(name)
            if not cls_path:
                logger.warning(f"StrategyEngine: unknown strategy '{name}'.")
                continue
            try:
                module_path, cls_name = cls_path.rsplit(".", 1)
                module = importlib.import_module(module_path)
                cls = getattr(module, cls_name)
                params = self._cfg.get(name, {})
                instance = cls(params)
                self._strategies.append(instance)
                logger.info(f"StrategyEngine: loaded {cls_name} ({name}).")
            except Exception as exc:
                logger.error(f"StrategyEngine: failed to load '{name}': {exc}")

    def set_symbols(self, symbols: list[str]) -> None:
        """Set the universe of symbols to run strategies on."""
        self._symbols = symbols

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the strategy engine.  Call after load_strategies()."""
        self._running = True
        logger.info("StrategyEngine: started.")
        # Register candle callbacks for each timeframe used by strategies
        timeframes_needed: set[str] = {s.timeframe for s in self._strategies}
        for tf in timeframes_needed:
            self._data_feed.on_new_candle(tf, self._on_candle)

    def stop(self) -> None:
        self._running = False
        logger.info("StrategyEngine: stopped.")

    def _on_candle(self, symbol: str, candle: pd.Series) -> None:
        """Called synchronously when a new candle closes; schedules async processing."""
        if not self._running:
            return
        asyncio.ensure_future(self._process_candle(symbol, candle))

    async def _process_candle(self, symbol: str, candle: pd.Series) -> None:
        """Run all strategies on the new candle and enqueue valid signals."""
        if symbol not in self._symbols:
            return

        kill = self._risk.check_kill_switches()
        if kill:
            logger.warning(f"StrategyEngine: kill switch active – {kill}.")
            return

        for strategy in self._strategies:
            # Only process if this candle's TF matches the strategy's TF
            tf = strategy.timeframe
            candles_df = self._data_feed.get_candles(symbol, tf)
            if len(candles_df) < strategy.required_history:
                continue

            try:
                tick = {
                    "symbol": symbol,
                    "ltp": float(candle.get("close", 0)),
                    "timestamp": candle.get("datetime"),
                }
                signal = strategy.generate_signal(candles_df, tick)
                if signal is None:
                    continue

                # Validate with risk manager
                if self._risk.validate_signal(signal, self._portfolio):
                    await self.signal_queue.put(signal)
                    logger.info(
                        f"StrategyEngine: signal {signal.direction} {signal.symbol} "
                        f"@ {signal.entry_price:.2f} [{signal.strategy}]"
                    )
                else:
                    logger.debug(f"StrategyEngine: signal rejected by risk manager [{symbol}].")
            except Exception as exc:
                logger.error(f"StrategyEngine: error in {strategy.name} for {symbol}: {exc}")

    # ------------------------------------------------------------------
    # Manual tick injection (for testing / paper trading without live feed)
    # ------------------------------------------------------------------

    async def on_tick(self, symbol: str, tick: dict[str, Any]) -> None:
        """Manually inject a tick update (useful for paper trading replay)."""
        self._data_feed.process_tick(tick)
