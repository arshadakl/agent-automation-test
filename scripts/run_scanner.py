"""CLI pre-market stock scanner runner."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import yaml
from loguru import logger
from dotenv import load_dotenv


def _load_config(path: str = "config/settings.yaml") -> dict:
    load_dotenv()
    with open(path) as fh:
        raw = fh.read()
    return yaml.safe_load(os.path.expandvars(raw))


def _load_universe(path: str = "config/stock_universe.yaml") -> list[dict]:
    with open(path) as fh:
        data = yaml.safe_load(fh)
    universe = []
    for sector, stocks in data.get("stocks", {}).items():
        for s in stocks:
            s["sector"] = sector
            universe.append(s)
    return universe


async def main(use_cache: bool = False) -> None:
    logger.add(sys.stdout, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")
    cfg = _load_config()
    universe = _load_universe()

    from core.scanner import Scanner
    scanner = Scanner(cfg.get("scanner", {}), broker=None)
    scanner.load_universe(universe)

    logger.info(f"Running scanner on {len(universe)} stocks…")
    candidates = await scanner.scan(use_cache=use_cache)

    if not candidates:
        logger.warning("No candidates found. Try providing cached data with --cache.")
        return

    # Print results using Rich if available
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box

        console = Console()
        table = Table(title="📊 Pre-Market Scan Results", box=box.ROUNDED, show_lines=True)
        table.add_column("#", style="dim")
        table.add_column("Symbol", style="bold cyan")
        table.add_column("Sector", style="yellow")
        table.add_column("Close (₹)", justify="right")
        table.add_column("ATR%", justify="right", style="green")
        table.add_column("Avg Vol (L)", justify="right")
        table.add_column("Turnover (Cr)", justify="right")

        for i, c in enumerate(candidates, 1):
            table.add_row(
                str(i),
                c["symbol"],
                c.get("sector", "-"),
                f"{c['close']:.2f}",
                f"{c['atr_percent']:.2f}%",
                f"{c['avg_volume'] / 1e5:.1f}L",
                f"₹{c['avg_turnover_cr']:.1f}Cr",
            )
        console.print(table)
    except ImportError:
        print("\n=== Pre-Market Scan Results ===")
        print(f"{'#':3} {'Symbol':12} {'Close':8} {'ATR%':6} {'Avg Vol':10} {'Turnover Cr':12}")
        print("-" * 60)
        for i, c in enumerate(candidates, 1):
            print(f"{i:3} {c['symbol']:12} {c['close']:8.2f} {c['atr_percent']:6.2f}% {c['avg_volume']:10,d} {c['avg_turnover_cr']:12.1f}")

    logger.info(f"\nTop {len(candidates)} stocks selected for today's trading.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="AlgoTrader India – pre-market scanner")
    parser.add_argument("--cache", action="store_true", help="Use cached historical data")
    args = parser.parse_args()
    asyncio.run(main(use_cache=args.cache))
