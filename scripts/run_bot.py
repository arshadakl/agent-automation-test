"""Main bot runner – initialises all components and starts the trading loop."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

# ---------------------------------------------------------------------------
# Load .env and config
# ---------------------------------------------------------------------------

load_dotenv()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml


def _load_config(path: str = "config/settings.yaml") -> dict:
    with open(path) as fh:
        raw = fh.read()
    # Expand environment variables
    raw = os.path.expandvars(raw)
    return yaml.safe_load(raw)


def _load_universe(path: str = "config/stock_universe.yaml") -> list[dict]:
    with open(path) as fh:
        data = yaml.safe_load(fh)
    universe = []
    for sector, stocks in data.get("stocks", {}).items():
        for s in stocks:
            s["sector"] = sector
            universe.append(s)
    return universe


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(cfg_path: str = "config/settings.yaml", use_web_ui: bool = False) -> None:
    cfg = _load_config(cfg_path)
    universe = _load_universe()

    mode = cfg["trading"]["mode"]
    initial_capital = cfg["trading"]["paper_capital"]

    logger.add("data/logs/algotrader_{time}.log", rotation="1 day", retention="7 days", level="INFO")
    logger.info(f"AlgoTrader starting in {mode.upper()} mode with ₹{initial_capital:,.0f} capital.")

    # ----- Broker -----
    from core.broker import AngelBroker
    broker_cfg = cfg.get("broker", {})
    broker = AngelBroker(broker_cfg)
    logged_in = await broker.login()
    if not logged_in:
        logger.error("Failed to log in to Angel One. Exiting.")
        sys.exit(1)

    # ----- Portfolio & Risk Manager -----
    from core.portfolio import Portfolio
    from core.risk_manager import RiskManager
    portfolio = Portfolio(initial_capital=initial_capital)
    risk = RiskManager(cfg.get("risk", {}), initial_capital=initial_capital)

    # ----- Paper Trader -----
    from core.paper_trader import PaperTrader
    paper_trader: PaperTrader | None = None
    if mode == "paper":
        paper_trader = PaperTrader(db_path="data/paper_trades.db", initial_capital=initial_capital)
        paper_trader.connect()

    # ----- Scanner -----
    from core.scanner import Scanner
    scanner = Scanner(cfg.get("scanner", {}), broker=broker)
    scanner.load_universe(universe)

    logger.info("Running pre-market scanner…")
    candidates = await scanner.scan(use_cache=False)
    if not candidates:
        logger.warning("Scanner returned no candidates; using default universe subset.")
        candidates = universe[:10]

    symbols = [c["symbol"] for c in candidates]
    symbol_tokens = [{"symbol": c["symbol"], "token": c.get("token", ""), "exchange": c.get("exchange", "NSE")} for c in candidates]
    logger.info(f"Trading universe: {symbols}")

    # ----- Data Feed -----
    from core.data_feed import DataFeed
    data_feed = DataFeed(broker=broker)
    data_feed.subscribe(symbols)

    # Pre-load some historical candles for strategy warm-up
    for sym_info in candidates[:5]:
        from datetime import datetime, timedelta
        try:
            df = await broker.get_historical(
                sym_info["symbol"], sym_info.get("token", ""),
                "FIVE_MINUTE",
                datetime.now() - timedelta(days=5),
                datetime.now(),
            )
            if not df.empty:
                data_feed.inject_historical(sym_info["symbol"], "5m", df)
        except Exception as exc:
            logger.warning(f"Could not pre-load history for {sym_info['symbol']}: {exc}")

    # ----- Execution Engine -----
    from core.execution import ExecutionEngine
    execution = ExecutionEngine(
        broker=broker,
        risk_manager=risk,
        portfolio=portfolio,
        config=cfg["trading"],
        paper_trader=paper_trader,
    )

    # ----- Strategy Engine -----
    from core.strategy_engine import StrategyEngine
    strategy_engine = StrategyEngine(cfg.get("strategies", {}), risk, portfolio, data_feed)
    strategy_engine.load_strategies()
    strategy_engine.set_symbols(symbols)

    # ----- Scheduler -----
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        scheduler = AsyncIOScheduler()

        # Token refresh every 30 minutes
        scheduler.add_job(broker.refresh_token, "interval", minutes=30)

        # Force square-off check
        sq_h, sq_m = map(int, cfg["schedule"]["square_off_check"].split(":"))
        scheduler.add_job(
            lambda: asyncio.ensure_future(execution.square_off_all("SCHEDULED_SQUAREOFF")),
            "cron", hour=sq_h, minute=sq_m,
        )

        # Daily summary at 15:35
        if paper_trader:
            scheduler.add_job(paper_trader.generate_daily_summary, "cron", hour=15, minute=35)

        scheduler.start()
        logger.info("Scheduler started.")
    except ImportError:
        logger.warning("APScheduler not installed; scheduling disabled.")
        scheduler = None

    # ----- Notifications -----
    notif_cfg = cfg.get("notifications", {})
    tg_bot = None
    if notif_cfg.get("enabled") and notif_cfg.get("telegram", {}).get("bot_token"):
        try:
            from telegram import Bot
            tg_bot = Bot(token=notif_cfg["telegram"]["bot_token"])
            logger.info("Telegram notifications enabled.")
        except ImportError:
            logger.warning("python-telegram-bot not installed; notifications disabled.")

    # ----- Dashboard -----
    shutdown_event = asyncio.Event()

    def _signal_handler(sig: int, frame: object) -> None:
        logger.info(f"Received signal {sig}; shutting down…")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # ----- Start all components -----
    tasks = [
        asyncio.create_task(strategy_engine.start(), name="strategy_engine"),
        asyncio.create_task(data_feed.start(symbol_tokens), name="data_feed"),
    ]

    # Signal consumer task
    async def consume_signals() -> None:
        while not shutdown_event.is_set():
            try:
                signal_obj = await asyncio.wait_for(strategy_engine.signal_queue.get(), timeout=1.0)
                qty = risk.calculate_position_size(
                    portfolio.available_capital,
                    signal_obj.entry_price,
                    signal_obj.stop_loss,
                )
                if qty > 0:
                    await execution.execute_signal(signal_obj, qty)
                    if tg_bot:
                        chat_id = notif_cfg["telegram"]["chat_id"]
                        msg = (
                            f"🚦 Signal: {signal_obj.direction} {signal_obj.symbol}\n"
                            f"Entry: {signal_obj.entry_price:.2f}  SL: {signal_obj.stop_loss:.2f}\n"
                            f"Target: {signal_obj.target_1:.2f}  Strategy: {signal_obj.strategy}"
                        )
                        await tg_bot.send_message(chat_id=chat_id, text=msg)
            except asyncio.TimeoutError:
                continue
            except Exception as exc:
                logger.error(f"Signal consumer error: {exc}")

    tasks.append(asyncio.create_task(consume_signals(), name="signal_consumer"))

    if use_web_ui:
        from dashboard.web_ui import create_app, run_server
        web_app = create_app(portfolio, risk, paper_trader, data_feed)
        import threading
        web_thread = threading.Thread(target=run_server, args=(web_app,), daemon=True)
        web_thread.start()
        logger.info("Web dashboard running on http://localhost:8000")
    else:
        from dashboard.terminal_ui import TerminalUI
        ui = TerminalUI(portfolio, risk, execution, mode=mode)
        tasks.append(asyncio.create_task(ui.run(), name="terminal_ui"))

    await shutdown_event.wait()

    # ----- Graceful shutdown -----
    logger.info("Shutting down…")
    strategy_engine.stop()
    data_feed.stop()
    if risk.should_square_off() or True:
        await execution.square_off_all("SHUTDOWN")
    if paper_trader:
        summary = paper_trader.generate_daily_summary()
        logger.info(f"Daily summary: {summary}")
        paper_trader.close()
    if scheduler:
        scheduler.shutdown()

    for task in tasks:
        task.cancel()

    logger.info("AlgoTrader stopped.")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AlgoTrader India – run the live/paper bot.")
    parser.add_argument("--config", default="config/settings.yaml", help="Path to settings.yaml")
    parser.add_argument("--web", action="store_true", help="Launch web dashboard instead of terminal UI")
    args = parser.parse_args()

    asyncio.run(main(cfg_path=args.config, use_web_ui=args.web))
