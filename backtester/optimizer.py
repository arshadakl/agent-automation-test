"""Strategy parameter optimizer – grid search and walk-forward."""

from __future__ import annotations

import itertools
from copy import deepcopy
from typing import Any, Type

import pandas as pd
from loguru import logger

from backtester.engine import BacktestEngine, BacktestResult
from strategies.base_strategy import BaseStrategy


class StrategyOptimizer:
    """Grid-search and walk-forward optimizer for strategy parameters.

    Usage:
        optimizer = StrategyOptimizer(ORBStrategy, df)
        best_params, results_df = optimizer.grid_search(
            param_grid={"risk_reward": [1.5, 2.0, 2.5], "volume_multiplier": [1.2, 1.5]}
        )
    """

    def __init__(
        self,
        strategy_cls: Type[BaseStrategy],
        df: pd.DataFrame,
        initial_capital: float = 100_000.0,
        symbol: str = "SYMBOL",
    ) -> None:
        self._cls = strategy_cls
        self._df = df.copy()
        self._initial_capital = initial_capital
        self._symbol = symbol

    # ------------------------------------------------------------------
    # Grid search
    # ------------------------------------------------------------------

    def grid_search(
        self,
        param_grid: dict[str, list[Any]],
        metric: str = "sharpe_ratio",
    ) -> tuple[dict[str, Any], pd.DataFrame]:
        """Exhaustive grid search over *param_grid*.

        Returns:
            best_params: dict of the best parameter combination
            results_df: DataFrame with all combinations and their metrics
        """
        keys = list(param_grid.keys())
        combinations = list(itertools.product(*[param_grid[k] for k in keys]))
        logger.info(f"StrategyOptimizer: grid search over {len(combinations)} combinations.")

        records = []
        for combo in combinations:
            params = dict(zip(keys, combo))
            try:
                strategy = self._cls(params)
                engine = BacktestEngine(strategy, self._initial_capital)
                result = engine.run(self._df, self._symbol)
                row = {**params, **self._result_to_dict(result)}
                records.append(row)
            except Exception as exc:
                logger.debug(f"StrategyOptimizer: combo {params} failed: {exc}")

        if not records:
            return {}, pd.DataFrame()

        results_df = pd.DataFrame(records)
        best_row = results_df.sort_values(metric, ascending=False).iloc[0]
        best_params = {k: best_row[k] for k in keys}

        logger.info(f"StrategyOptimizer: best params = {best_params}, {metric} = {best_row[metric]:.3f}")
        return best_params, results_df

    # ------------------------------------------------------------------
    # Walk-forward optimization
    # ------------------------------------------------------------------

    def walk_forward(
        self,
        param_grid: dict[str, list[Any]],
        n_splits: int = 5,
        train_pct: float = 0.7,
        metric: str = "sharpe_ratio",
    ) -> tuple[list[dict[str, Any]], pd.DataFrame]:
        """Walk-forward analysis.

        Splits data into *n_splits* windows.  For each window, grid-searches
        on the in-sample (train) portion and evaluates on the out-of-sample
        (test) portion.

        Returns:
            wf_results: list of per-fold best params + OOS metrics
            summary_df: DataFrame summarising all folds
        """
        df = self._df.reset_index(drop=True)
        n = len(df)
        window = n // n_splits
        wf_results: list[dict[str, Any]] = []

        for fold in range(n_splits):
            start = fold * window
            end = min(start + window, n)
            train_end = start + int((end - start) * train_pct)

            train_df = df.iloc[start:train_end].copy()
            test_df = df.iloc[train_end:end].copy()

            if len(train_df) < 50 or len(test_df) < 20:
                continue

            logger.info(f"StrategyOptimizer: walk-forward fold {fold + 1}/{n_splits}")

            # Optimise on train
            self._df = train_df
            best_params, _ = self.grid_search(param_grid, metric)
            if not best_params:
                continue

            # Evaluate on test
            self._df = test_df
            strategy = self._cls(best_params)
            engine = BacktestEngine(strategy, self._initial_capital)
            oos_result = engine.run(test_df, self._symbol)

            wf_results.append({
                "fold": fold + 1,
                "best_params": best_params,
                **{f"oos_{k}": v for k, v in self._result_to_dict(oos_result).items()},
            })

        # Restore
        self._df = df

        summary_df = pd.DataFrame(wf_results)
        logger.info(f"StrategyOptimizer: walk-forward complete. {len(wf_results)} folds evaluated.")
        return wf_results, summary_df

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _result_to_dict(result: BacktestResult) -> dict[str, Any]:
        return {
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "total_return_pct": result.total_return_pct,
            "cagr": result.cagr,
            "sharpe_ratio": result.sharpe_ratio,
            "max_drawdown_pct": result.max_drawdown_pct,
            "avg_pnl": result.avg_pnl,
        }
