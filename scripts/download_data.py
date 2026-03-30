"""CLI data downloader – downloads historical OHLCV and saves as Parquet."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argparse
import os
import yaml
from loguru import logger
from dotenv import load_dotenv


def _load_config(path: str = "config/settings.yaml") -> dict:
    load_dotenv()
    with open(path) as fh:
        raw = fh.read()
    return yaml.safe_load(os.path.expandvars(raw))


def _load_symbol_tokens(universe_path: str = "config/stock_universe.yaml") -> dict[str, str]:
    """Return a symbol → token mapping from the universe config."""
    with open(universe_path) as fh:
        data = yaml.safe_load(fh)
    mapping: dict[str, str] = {}
    for _, stocks in data.get("stocks", {}).items():
        for s in stocks:
            mapping[s["symbol"]] = s.get("token", "")
    return mapping


async def download(
    symbols: list[str],
    from_date: datetime,
    to_date: datetime,
    interval: str,
    output_dir: str,
    broker: "AngelBroker",  # noqa: F821
    token_map: dict[str, str],
) -> None:
    from backtester.data_loader import DataLoader
    loader = DataLoader(cache_dir=output_dir, broker=broker)

    total = len(symbols)
    for idx, symbol in enumerate(symbols, 1):
        token = token_map.get(symbol, "")
        logger.info(f"[{idx}/{total}] Downloading {symbol} {interval} …")
        try:
            df = loader.load(
                symbol=symbol,
                interval=interval,
                from_date=from_date,
                to_date=to_date,
                token=token,
                force_download=True,
            )
            if df.empty:
                logger.warning(f"  ↳ No data returned for {symbol}.")
            else:
                logger.info(f"  ↳ {len(df)} rows saved.")
        except Exception as exc:
            logger.error(f"  ↳ Error downloading {symbol}: {exc}")
        await asyncio.sleep(0.5)  # Throttle API calls


async def main() -> None:
    parser = argparse.ArgumentParser(description="AlgoTrader India – historical data downloader")
    parser.add_argument("--symbols", nargs="+", help="Symbols to download (e.g. RELIANCE TCS). Omit for full universe.")
    parser.add_argument("--from", dest="from_date", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--interval", default="5m", choices=["1m", "5m", "15m", "1d"])
    parser.add_argument("--output", default="data/historical", help="Output directory for Parquet files")
    parser.add_argument("--config", default="config/settings.yaml")
    args = parser.parse_args()

    logger.add(sys.stdout, level="INFO")
    cfg = _load_config(args.config)

    to_date = datetime.strptime(args.to_date, "%Y-%m-%d") if args.to_date else datetime.now()
    from_date = datetime.strptime(args.from_date, "%Y-%m-%d") if args.from_date else (to_date - timedelta(days=180))

    token_map = _load_symbol_tokens()

    if args.symbols:
        symbols = args.symbols
    else:
        symbols = list(token_map.keys())

    logger.info(f"Downloading {len(symbols)} symbols | {args.interval} | {from_date.date()} → {to_date.date()}")
    logger.info(f"Output: {args.output}")

    # Login
    from core.broker import AngelBroker
    broker = AngelBroker(cfg.get("broker", {}))
    logged_in = await broker.login()
    if not logged_in:
        logger.error("Login failed. Exiting.")
        sys.exit(1)

    os.makedirs(args.output, exist_ok=True)
    await download(symbols, from_date, to_date, args.interval, args.output, broker, token_map)
    logger.info("Download complete.")


if __name__ == "__main__":
    asyncio.run(main())
