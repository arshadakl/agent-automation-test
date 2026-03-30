# 🚀 AlgoTrader India

> **Production-grade intraday algorithmic trading bot for the Indian stock market (NSE), powered by Angel One SmartAPI.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## ⚠️ Disclaimer

**This software is for educational purposes only.** Algorithmic trading carries significant financial risk. Past performance does not guarantee future results. You may lose all invested capital. Use at your own risk. The authors accept no liability for financial losses. Always test extensively in paper trading mode before risking real capital.

---

## ✨ Features

- **5 built-in strategies**: ORB, VWAP Reversion, Momentum Breakout, EMA Crossover, Gap Fill
- **Paper trading** with SQLite backend and realistic charge simulation
- **Live trading** via Angel One SmartAPI (TOTP authentication)
- **Pre-market scanner** filtering 50 NIFTY stocks by ATR, volume, turnover
- **Advanced risk management**: kill switches, trailing stops, position sizing, time rules
- **Vectorized backtester** with Indian market charges (STT, GST, stamp duty, etc.)
- **Strategy optimizer**: grid search + walk-forward analysis
- **Terminal dashboard** (Rich/Textual) and **web dashboard** (FastAPI + HTMX)
- **Telegram notifications** for signals, fills, and daily summaries
- **APScheduler** for market-hours automation
- **Parquet data caching** for fast historical data access

---

## 📋 Prerequisites

- Python 3.11 or higher
- Angel One demat + trading account with API access
- Angel One API key from [SmartAPI portal](https://smartapi.angelbroking.com/)
- (Optional) Telegram bot token for notifications

---

## 🛠️ Installation

```bash
# Clone the repository
git clone https://github.com/your-org/algotrader-india.git
cd algotrader-india

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate    # Windows

# Install dependencies
pip install -r requirements.txt
```

---

## ⚙️ Configuration

### Step 1 – Environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
ANGEL_CLIENT_ID=your_client_id
ANGEL_PASSWORD=your_trading_password
ANGEL_TOTP_SECRET=your_totp_base32_secret   # from Angel One TOTP setup
ANGEL_API_KEY=your_api_key
TELEGRAM_BOT_TOKEN=optional_telegram_token
TELEGRAM_CHAT_ID=optional_chat_id
```

### Step 2 – Settings

Edit `config/settings.yaml` to adjust:

| Section | Key settings |
|---------|-------------|
| `trading.mode` | `"paper"` (default) or `"live"` |
| `trading.paper_capital` | Starting capital for paper trading |
| `risk.*` | Kill switches, position sizing, time rules |
| `scanner.*` | Stock filters (volume, ATR, price range) |
| `strategies.enabled` | List of active strategies |
| `notifications.enabled` | Set to `true` to enable Telegram alerts |

---

## 🚀 Usage

### Run the trading bot

```bash
# Paper trading with terminal dashboard (default)
python scripts/run_bot.py

# Paper trading with web dashboard (http://localhost:8000)
python scripts/run_bot.py --web

# Custom config file
python scripts/run_bot.py --config config/settings.yaml
```

### Run a backtest

```bash
python scripts/run_backtest.py \
  --strategy orb \
  --symbol RELIANCE \
  --from 2024-01-01 \
  --to 2024-06-30 \
  --capital 100000 \
  --interval 15m

# Output: data/reports/backtest_report.html  +  data/reports/trades.csv
```

### Run the pre-market scanner

```bash
python scripts/run_scanner.py

# Use locally cached data (offline mode)
python scripts/run_scanner.py --cache
```

### Download historical data

```bash
# Download 5m data for specific symbols
python scripts/download_data.py --symbols RELIANCE TCS INFY --interval 5m --from 2024-01-01

# Download full NIFTY50 universe (1 day candles)
python scripts/download_data.py --interval 1d --from 2023-01-01
```

### Run tests

```bash
pytest tests/ -v
```

---

## 📊 Strategy Descriptions

### 1. Opening Range Breakout (ORB) – `orb`

**Timeframe**: 15m  
**Logic**: Waits for the first 15-minute candle (09:15–09:30) to define the opening range. Generates a **LONG** signal when price breaks above the ORB high with volume > 1.5× average, and a **SHORT** signal on a breakdown below the ORB low. Filters out indecisive candles via a body-ratio check.

**Parameters**: `volume_multiplier`, `min_body_ratio`, `risk_reward`

---

### 2. VWAP Reversion – `vwap_reversion`

**Timeframe**: 5m  
**Logic**: Mean-reversion strategy. Goes **LONG** when price dips to VWAP − 1 std dev and RSI < 35 (oversold bounce), and **SHORT** when price rises to VWAP + 1 std dev with RSI > 65. Target is the VWAP centre line.

**Parameters**: `std_dev_bands`, `rsi_oversold`, `rsi_overbought`

---

### 3. Momentum Breakout – `momentum_breakout`

**Timeframe**: 5m  
**Logic**: Identifies consolidation (last N candles' ATR < 50% of average ATR), then signals a breakout when price exits the range with 2× volume surge. ADX > 25 confirms trend strength; EMA 9/21 crossover confirms direction.

**Parameters**: `consolidation_candles`, `volume_surge_multiplier`, `adx_threshold`

---

### 4. EMA Crossover – `ema_crossover`

**Timeframe**: 5m  
**Logic**: Classic EMA 9/21 crossover. **LONG** when EMA9 crosses above EMA21 with price above VWAP and RSI between 40–60 (neutral zone). Avoids chasing overbought/oversold conditions.

**Parameters**: `ema_fast`, `ema_slow`, `rsi_low`, `rsi_high`

---

### 5. Gap Fill – `gap_fill`

**Timeframe**: 5m  
**Logic**: Trades gap reversals in the first 30 minutes. If today's open > prev close by ≥ 1% (gap up) and price starts filling back down, goes **SHORT** toward prev close. Vice versa for gap-down scenarios.

**Parameters**: `gap_threshold_pct`, `sl_pct`

---

## 🛡️ Risk Management

The `RiskManager` enforces the following rules on every signal:

| Rule | Default |
|------|---------|
| Max capital usage | 60% |
| Per-trade risk | 1.5% of capital |
| Max position size | 15% of capital |
| Max daily loss | 3.0% → kill switch |
| Consecutive losses | 3 → 30 min cooldown |
| Max SL width | 2.0% |
| No new trades after | 14:30 IST |
| Force square-off | 15:10 IST |
| No trade first N min | 3 min (before 09:18) |
| Trailing stop | Enabled (1% trigger, 0.5% trail) |
| Drawdown > 5% | Reduce sizes by 50% |

---

## 📄 Paper Trading vs Live Trading

| Feature | Paper Trading | Live Trading |
|---------|--------------|--------------|
| Orders | Simulated against LTP | Placed via SmartAPI |
| P&L | Estimated (charges ~0.05%) | Real money |
| Database | SQLite (`data/paper_trades.db`) | Order book via API |
| Risk | None | Real capital at risk |
| Switch | `trading.mode: paper` | `trading.mode: live` |

> **Always start in paper mode and validate strategy performance over at least 30 trading days before switching to live.**

---

## 🏗️ Architecture Overview

```
algotrader-india/
├── config/              # YAML configuration
│   ├── settings.yaml    # All parameters
│   └── stock_universe.yaml  # 50 NIFTY stocks with tokens
│
├── core/                # Core trading engine
│   ├── broker.py        # Angel One SmartAPI wrapper
│   ├── data_feed.py     # WebSocket tick → OHLCV candles
│   ├── scanner.py       # Pre-market stock scanner
│   ├── strategy_engine.py   # Strategy orchestrator
│   ├── risk_manager.py  # Kill switches + position sizing
│   ├── execution.py     # Order placement + trailing stops
│   ├── paper_trader.py  # SQLite paper trading simulator
│   └── portfolio.py     # Position + P&L tracker
│
├── strategies/          # Trading strategies
│   ├── base_strategy.py # Abstract base + Signal dataclass
│   ├── orb.py           # Opening Range Breakout
│   ├── vwap_reversion.py
│   ├── momentum_breakout.py
│   ├── ema_crossover.py
│   └── gap_fill.py
│
├── backtester/          # Backtesting framework
│   ├── engine.py        # Vectorized backtest engine
│   ├── data_loader.py   # Parquet cache + API download
│   ├── report.py        # HTML/CSV report generation
│   └── optimizer.py     # Grid search + walk-forward
│
├── dashboard/
│   ├── terminal_ui.py   # Rich live terminal dashboard
│   └── web_ui.py        # FastAPI + HTMX web dashboard
│
├── scripts/             # CLI entry points
│   ├── run_bot.py
│   ├── run_backtest.py
│   ├── run_scanner.py
│   └── download_data.py
│
├── tests/               # pytest test suite
├── data/                # Runtime data (gitignored)
│   ├── historical/      # Parquet files
│   ├── logs/            # Loguru logs
│   └── reports/         # Backtest HTML/CSV
└── requirements.txt
```

**Data flow**:
```
Market open
  → Scanner (08:50) selects top 15 stocks
  → DataFeed subscribes to WebSocket ticks
  → Ticks → 1m candles → 5m/15m aggregation
  → StrategyEngine dispatches to strategies
  → Signal → RiskManager validates
  → ExecutionEngine places orders (paper or live)
  → Portfolio tracks P&L
  → Dashboard displays real-time state
  → 15:10 → force square-off
  → 15:35 → daily summary
```

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-strategy`)
3. Write tests for your changes
4. Run `pytest tests/` and ensure all tests pass
5. Submit a pull request

---

## 📜 License

MIT License. See [LICENSE](LICENSE) for details.

---

*Built with ❤️ for the Indian trading community.*
