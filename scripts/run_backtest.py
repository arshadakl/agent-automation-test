"""CLI backtest runner."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger


def main() -> None:
    parser = argparse.ArgumentParser(description="AlgoTrader India – backtest runner")
    parser.add_argument("--strategy", required=True, choices=["orb", "vwap_reversion", "momentum_breakout", "ema_crossover", "gap_fill"])
    parser.add_argument("--symbol", required=True, help="NSE symbol, e.g. RELIANCE")
    parser.add_argument("--token", default="", help="Angel One instrument token")
    parser.add_argument("--from", dest="from_date", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=100_000.0, help="Initial capital (INR)")
    parser.add_argument("--interval", default="5m", choices=["1m", "5m", "15m", "1d"])
    parser.add_argument("--output", default="data/reports/backtest_report.html", help="HTML report output path")
    parser.add_argument("--csv", default="data/reports/trades.csv", help="CSV trade log output path")
    args = parser.parse_args()

    to_date = datetime.strptime(args.to_date, "%Y-%m-%d") if args.to_date else datetime.now()
    from_date = datetime.strptime(args.from_date, "%Y-%m-%d") if args.from_date else (to_date - timedelta(days=180))

    logger.add(sys.stdout, level="INFO")
    logger.info(f"Backtesting {args.strategy} on {args.symbol} | {from_date.date()} → {to_date.date()}")

    # Load data
    from backtester.data_loader import DataLoader
    loader = DataLoader(cache_dir="data/historical")
    df = loader.load(
        symbol=args.symbol,
        interval=args.interval,
        from_date=from_date,
        to_date=to_date,
        token=args.token,
        force_download=False,
    )

    if df.empty:
        logger.error(f"No data available for {args.symbol}. Check the cache or provide a token for download.")
        sys.exit(1)

    logger.info(f"Loaded {len(df)} candles for {args.symbol}.")

    # Load strategy
    strategy_map = {
        "orb": ("strategies.orb", "ORBStrategy"),
        "vwap_reversion": ("strategies.vwap_reversion", "VWAPReversionStrategy"),
        "momentum_breakout": ("strategies.momentum_breakout", "MomentumBreakoutStrategy"),
        "ema_crossover": ("strategies.ema_crossover", "EMACrossoverStrategy"),
        "gap_fill": ("strategies.gap_fill", "GapFillStrategy"),
    }
    module_path, cls_name = strategy_map[args.strategy]
    import importlib
    module = importlib.import_module(module_path)
    strategy_cls = getattr(module, cls_name)
    strategy = strategy_cls()

    # Run backtest
    from backtester.engine import BacktestEngine
    engine = BacktestEngine(strategy, initial_capital=args.capital)
    result = engine.run(df, symbol=args.symbol)

    # Report
    from backtester.report import BacktestReport
    report = BacktestReport(result)
    report.print_summary()
    html_path = report.to_html(args.output)
    csv_path = report.to_csv(args.csv)

    if html_path:
        logger.info(f"HTML report saved: {html_path}")
    if csv_path:
        logger.info(f"CSV trades saved: {csv_path}")


if __name__ == "__main__":
    main()
