# AlgoTrader India — Intraday Auto Trading Bot

## Architecture Plan v1.0

> **Broker:** Angel One SmartAPI
> **Language:** Python 3.11+
> **Deployment:** Local first → VPS later
> **Market:** NSE/BSE Intraday (Equity + F&O)

---

## 1. System Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        AlgoTrader India                             │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │  Stock    │  │ Strategy │  │   Risk   │  │Execution │           │
│  │ Scanner  │──│  Engine  │──│ Manager  │──│  Engine  │           │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘           │
│       │              │              │              │                │
│       ▼              ▼              ▼              ▼                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │   Data   │  │Backtester│  │ Portfolio │  │  Paper   │           │
│  │  Feed    │  │          │  │ Tracker  │  │ Trading  │           │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘           │
│                                                                     │
│  ┌──────────────────────────────────────────────────────┐          │
│  │              Dashboard / Monitoring                   │          │
│  │         (Terminal UI + Optional Web UI)                │          │
│  └──────────────────────────────────────────────────────┘          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Project Structure

```
algotrader-india/
├── config/
│   ├── settings.yaml          # Master config (risk %, capital, strategies)
│   ├── credentials.env        # Angel One API keys (gitignored)
│   └── stock_universe.yaml    # Watchlist, sectors, filters
│
├── core/
│   ├── __init__.py
│   ├── broker.py              # Angel One SmartAPI wrapper
│   ├── data_feed.py           # Real-time + historical data manager
│   ├── scanner.py             # Pre-market stock picker
│   ├── strategy_engine.py     # Strategy runner + signal generator
│   ├── risk_manager.py        # Position sizing, SL, daily limits
│   ├── execution.py           # Order placement + management
│   ├── paper_trader.py        # Paper trading simulator
│   └── portfolio.py           # Account balance + position tracker
│
├── strategies/
│   ├── __init__.py
│   ├── base_strategy.py       # Abstract base class
│   ├── orb.py                 # Opening Range Breakout
│   ├── vwap_reversion.py      # VWAP Mean Reversion
│   ├── momentum_breakout.py   # Volume + Momentum Breakout
│   ├── ema_crossover.py       # EMA 9/21 Crossover with filters
│   └── gap_fill.py            # Gap Fill Strategy
│
├── backtester/
│   ├── __init__.py
│   ├── engine.py              # Backtesting engine (vectorbt + custom)
│   ├── data_loader.py         # Historical data fetcher + cacher
│   ├── report.py              # Performance report generator
│   └── optimizer.py           # Parameter optimization
│
├── data/
│   ├── historical/            # Cached OHLCV data (SQLite/Parquet)
│   ├── logs/                  # Trade logs, system logs
│   ├── reports/               # Backtest HTML reports
│   └── paper_trades.db        # Paper trading journal (SQLite)
│
├── dashboard/
│   ├── terminal_ui.py         # Rich/Textual terminal dashboard
│   └── web_ui.py              # Optional: FastAPI + HTMX dashboard
│
├── scripts/
│   ├── run_bot.py             # Main entry point
│   ├── run_backtest.py        # Backtest runner
│   ├── run_scanner.py         # Standalone scanner
│   └── download_data.py       # Historical data downloader
│
├── tests/
│   ├── test_strategies.py
│   ├── test_risk_manager.py
│   └── test_scanner.py
│
├── requirements.txt
├── .env.example
├── plan.md                    # This file
└── README.md
```

---

## 3. Module Deep Dives

### 3.1 Broker Module (`core/broker.py`)

**Purpose:** Unified wrapper around Angel One SmartAPI

**Angel One SmartAPI provides:**
- REST API for orders, positions, holdings, funds
- WebSocket for real-time tick data
- Historical candle data (1min, 5min, 15min, daily)
- Order types: MARKET, LIMIT, SL, SL-M

**Key class:**
```python
class AngelBroker:
    def __init__(self, credentials: dict)
    
    # Auth
    async def login(self) -> bool           # TOTP-based login
    async def refresh_token(self) -> bool    # Auto token refresh
    
    # Account
    async def get_funds(self) -> dict        # Available margin/balance
    async def get_positions(self) -> list    # Current open positions
    async def get_orders(self) -> list       # Today's order book
    
    # Orders
    async def place_order(self, order: Order) -> str
    async def modify_order(self, order_id: str, params: dict) -> bool
    async def cancel_order(self, order_id: str) -> bool
    
    # Data
    async def get_historical(self, symbol, interval, from_dt, to_dt) -> pd.DataFrame
    async def subscribe_ticks(self, symbols: list, callback: Callable)
    async def get_ltp(self, symbols: list) -> dict
```

**Auth flow:**
1. Login with client_id + password + TOTP token
2. Receive JWT token (valid ~24hrs)
3. Auto-refresh before expiry
4. TOTP generation via `pyotp` library (no manual input needed)

---

### 3.2 Stock Scanner (`core/scanner.py`)

**Purpose:** Automatically pick 5-15 high-probability stocks daily before market open (9:00 AM)

**Scanning Pipeline:**
```
NSE Stock Universe (~1800 stocks)
        │
        ▼ Filter 1: Liquidity
    Avg daily volume > 5L shares
    Avg daily turnover > ₹10Cr
        │
        ▼ Filter 2: Price Range
    Stock price ₹100 - ₹5000
    (avoids penny stocks + high-capital stocks)
        │
        ▼ Filter 3: Volatility
    ATR(14) > 1.5% of price
    (needs enough movement for intraday)
        │
        ▼ Filter 4: Pre-Market Signals
    Gap up/down > 0.5%
    Pre-market volume spike
    Sector strength alignment
        │
        ▼ Filter 5: Technical Setup
    Near key EMA levels (9/21/50)
    RSI between 30-70 (avoid extremes)
    VWAP positioning from previous day
        │
        ▼
    Final Watchlist (5-15 stocks)
    Ranked by score (composite of all filters)
```

**Data sources (all free):**
- Angel One historical API for OHLCV
- NSE Bhavcopy (daily EOD data, free CSV download)
- Pre-market data via Angel One WebSocket (starts 9:00 AM)

**Scanner runs at:** 8:50 AM daily (cron/scheduler)

---

### 3.3 Strategy Engine (`core/strategy_engine.py`)

**Purpose:** Run multiple strategies in parallel, generate entry/exit signals

**Base Strategy Interface:**
```python
class BaseStrategy(ABC):
    name: str
    timeframe: str           # "1m", "5m", "15m"
    required_history: int    # Candles needed before first signal
    
    @abstractmethod
    def generate_signal(self, candles: pd.DataFrame, tick: dict) -> Signal | None
    
    @abstractmethod
    def get_stop_loss(self, entry_price: float, signal: Signal) -> float
    
    @abstractmethod
    def get_targets(self, entry_price: float, signal: Signal) -> list[float]
    
    @abstractmethod
    def backtest_params(self) -> dict  # Parameter ranges for optimization
```

**Signal object:**
```python
@dataclass
class Signal:
    strategy: str        # Strategy name
    symbol: str          # Stock symbol
    direction: str       # "LONG" or "SHORT"
    entry_price: float
    stop_loss: float
    target_1: float
    target_2: float | None
    confidence: float    # 0.0 - 1.0
    timestamp: datetime
    metadata: dict       # Strategy-specific data
```

#### Included Strategies

**Strategy 1: Opening Range Breakout (ORB)**
- Timeframe: 15-minute candle (9:15 - 9:30)
- Entry: Break above high or below low of first candle
- Confirmation: Volume > 1.5x average, candle body > 60% of range
- Stop Loss: Opposite end of opening range
- Target: 1:2 risk-reward, trail after 1:1
- Best for: High-gap, high-volume stocks

**Strategy 2: VWAP Mean Reversion**
- Timeframe: 5-minute candles
- Entry: Price touches VWAP + bounces with reversal candle
- Confirmation: RSI divergence, volume contraction then expansion
- Stop Loss: Beyond VWAP band (1 std dev)
- Target: Previous swing high/low
- Best for: Range-bound, high-liquidity stocks

**Strategy 3: Momentum Breakout**
- Timeframe: 5-minute candles
- Entry: Break of intraday consolidation with volume surge (>2x avg)
- Confirmation: EMA(9) > EMA(21), ADX > 25
- Stop Loss: Below consolidation zone
- Target: Measured move (height of consolidation)
- Best for: Trending stocks with sector momentum

**Strategy 4: EMA 9/21 Crossover (Filtered)**
- Timeframe: 5-minute candles
- Entry: EMA(9) crosses EMA(21) + price above VWAP (for long)
- Confirmation: Volume above average, SuperTrend alignment
- Stop Loss: EMA(21) or recent swing
- Target: Trailing stop with EMA(9)
- Best for: Trending market days (avoid on flat VIX)

**Strategy 5: Gap Fill Strategy**
- Timeframe: 5-minute candles
- Entry: Gap > 1% that starts filling within first 30 mins
- Confirmation: Volume declining on gap + increasing on fill
- Stop Loss: Beyond gap extreme
- Target: 50-80% gap fill level
- Best for: Stocks with history of gap fills

**Strategy Selection Logic:**
```python
# Daily strategy selection based on market regime
def select_strategies(market_data: dict) -> list[BaseStrategy]:
    india_vix = market_data["india_vix"]
    nifty_gap = market_data["nifty_gap_percent"]
    
    strategies = []
    
    if india_vix > 18:  # High volatility
        strategies.append(ORBStrategy())
        strategies.append(MomentumBreakout())
    elif india_vix < 13:  # Low volatility
        strategies.append(VWAPReversion())
        strategies.append(EMACrossover())
    else:  # Normal volatility
        strategies.append(ORBStrategy())
        strategies.append(VWAPReversion())
    
    if abs(nifty_gap) > 0.5:
        strategies.append(GapFillStrategy())
    
    return strategies
```

---

### 3.4 Risk Manager (`core/risk_manager.py`)

**Purpose:** The most critical module. Protects capital above all else.

**Configuration (settings.yaml):**
```yaml
risk:
  # Capital allocation
  max_capital_usage_percent: 60       # Use max 60% of total balance
  per_trade_risk_percent: 1.5         # Risk 1.5% of capital per trade
  max_position_size_percent: 15       # Max 15% of capital in one stock
  
  # Daily limits
  max_daily_loss_percent: 3.0         # Stop trading if 3% daily loss
  max_daily_trades: 10                # Max 10 trades per day
  max_open_positions: 5               # Max 5 simultaneous positions
  
  # Stop loss
  mandatory_stop_loss: true           # Every trade MUST have SL
  max_stop_loss_percent: 2.0          # SL can't be > 2% from entry
  trailing_stop_enabled: true
  trailing_stop_trigger: 1.0          # Activate trailing at 1% profit
  trailing_stop_distance: 0.5         # Trail at 0.5%
  
  # Time rules
  no_new_trades_after: "14:30"        # No new entries after 2:30 PM
  force_square_off_time: "15:10"      # Close all positions by 3:10 PM
  no_trade_first_minutes: 3           # Skip first 3 min (9:15-9:18)
```

**Risk Manager class:**
```python
class RiskManager:
    def __init__(self, config: dict, broker: AngelBroker)
    
    # Pre-trade checks
    def can_trade(self) -> tuple[bool, str]           # Overall trading allowed?
    def validate_signal(self, signal: Signal) -> bool  # Signal passes risk rules?
    def calculate_quantity(self, signal: Signal) -> int # Position size
    
    # Active monitoring
    def check_daily_pnl(self) -> float                # Current day P&L
    def check_drawdown(self) -> float                 # Current drawdown
    def enforce_square_off(self)                       # Time-based exit
    
    # Post-trade
    def update_trade_log(self, trade: Trade)
    def daily_report(self) -> dict
```

**Position sizing formula:**
```
Available Capital = Account Balance × max_capital_usage_percent
Risk Amount = Available Capital × per_trade_risk_percent
Stop Loss Distance = |Entry Price - Stop Loss Price|
Quantity = Risk Amount / Stop Loss Distance
Max Quantity = (Available Capital × max_position_size_percent) / Entry Price
Final Quantity = min(Quantity, Max Quantity)
```

**Example:**
```
Account Balance:     ₹1,00,000
Capital Usage (60%): ₹60,000
Risk per Trade (1.5%): ₹900
Stock Price:         ₹500
Stop Loss:           ₹490 (2% away)
SL Distance:         ₹10

Quantity = ₹900 / ₹10 = 90 shares
Max Qty = (₹60,000 × 15%) / ₹500 = 18 shares

Final Quantity = min(90, 18) = 18 shares
Actual Risk = 18 × ₹10 = ₹180 (0.18% of capital — safe)
```

**Kill switches:**
1. Daily loss > 3% → Stop all trading for the day
2. 3 consecutive losses → Pause 30 minutes
3. Drawdown > 5% from peak → Reduce position sizes by 50%
4. API error → Cancel all pending orders, square off

---

### 3.5 Execution Engine (`core/execution.py`)

**Purpose:** Convert validated signals into actual broker orders

**Order flow:**
```
Signal validated by Risk Manager
        │
        ▼
Check: Paper mode or Live mode?
        │
   ┌────┴────┐
   │         │
Paper      Live
   │         │
   ▼         ▼
Simulate   Place bracket order via Angel One
   │         │
   ▼         ▼
Log to     Monitor fills
paper_trades.db   │
   │         ▼
   ▼       Place SL + Target orders
Both: Update portfolio tracker
```

**Order types used:**
- **Entry:** LIMIT order (or MARKET if aggressive strategy)
- **Stop Loss:** SL-M (Stop Loss Market) — guaranteed exit
- **Target:** LIMIT sell order
- **Square-off:** MARKET order (time-based exit)

**Bracket order simulation (Angel One):**
Angel One doesn't natively support bracket orders for all segments. We simulate:
```python
async def place_trade(self, signal: Signal, quantity: int):
    # 1. Place entry order
    entry_order = await self.broker.place_order(
        symbol=signal.symbol,
        qty=quantity,
        order_type="LIMIT",
        price=signal.entry_price,
        transaction_type="BUY" if signal.direction == "LONG" else "SELL"
    )
    
    # 2. Wait for fill
    fill = await self.wait_for_fill(entry_order, timeout=60)
    
    if fill:
        # 3. Place SL order
        sl_order = await self.broker.place_order(
            symbol=signal.symbol,
            qty=quantity,
            order_type="SL-M",
            trigger_price=signal.stop_loss,
            transaction_type="SELL" if signal.direction == "LONG" else "BUY"
        )
        
        # 4. Place target order
        target_order = await self.broker.place_order(
            symbol=signal.symbol,
            qty=quantity,
            order_type="LIMIT",
            price=signal.target_1,
            transaction_type="SELL" if signal.direction == "LONG" else "BUY"
        )
        
        # 5. Monitor: cancel SL when target hits and vice versa
        await self.monitor_exit(sl_order, target_order)
```

---

### 3.6 Paper Trading (`core/paper_trader.py`)

**Purpose:** Simulate live trading using real-time prices without placing actual orders

**How it works:**
1. Receives same signals as live engine
2. Subscribes to real-time ticks for entered stocks
3. Simulates fills based on LTP (Last Traded Price)
4. Tracks P&L, win rate, drawdown in SQLite
5. Generates identical reports as live trading

**Paper trading database schema:**
```sql
CREATE TABLE paper_trades (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    symbol TEXT,
    direction TEXT,           -- LONG/SHORT
    strategy TEXT,
    entry_price REAL,
    exit_price REAL,
    quantity INTEGER,
    stop_loss REAL,
    target REAL,
    pnl REAL,
    pnl_percent REAL,
    exit_reason TEXT,         -- TARGET_HIT, SL_HIT, SQUARE_OFF, MANUAL
    holding_time_minutes INTEGER,
    status TEXT               -- OPEN, CLOSED
);

CREATE TABLE paper_daily_summary (
    date TEXT PRIMARY KEY,
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    gross_pnl REAL,
    charges_estimate REAL,    -- Brokerage + STT + GST estimate
    net_pnl REAL,
    max_drawdown REAL,
    capital_used REAL
);
```

**Switching modes:**
```yaml
# settings.yaml
trading:
  mode: "paper"     # "paper" or "live"
  paper_capital: 100000  # Virtual capital for paper trading
```

---

### 3.7 Backtesting Engine (`backtester/engine.py`)

**Purpose:** Test any strategy against historical data before deploying

**Architecture:**
```
Historical Data (Parquet/SQLite)
        │
        ▼
    Data Loader
    (1min/5min candles, 6-12 months)
        │
        ▼
    Strategy Instance
    (generates signals on historical candles)
        │
        ▼
    Trade Simulator
    (simulates fills, slippage, charges)
        │
        ▼
    Performance Analyzer
    (metrics, equity curve, drawdown)
        │
        ▼
    HTML Report
```

**Historical data sources (free):**
- Angel One SmartAPI — up to 2 years of candle data
- yfinance — daily data (limited for intraday)
- TVDatafeed — TradingView data (unofficial, free)

**Data caching:**
```python
# First run: download and cache
data_loader.download("RELIANCE", "5m", days=180)  # Saves to data/historical/

# Subsequent runs: load from cache
candles = data_loader.load("RELIANCE", "5m", "2025-01-01", "2025-06-30")
```

**Backtest execution:**
```python
class BacktestEngine:
    def __init__(self, strategy: BaseStrategy, risk_config: dict)
    
    def run(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        initial_capital: float = 100000,
        slippage_percent: float = 0.05,    # 0.05% slippage per trade
        commission_per_trade: float = 20,   # Angel One charges
    ) -> BacktestResult
    
    def optimize(
        self,
        param_grid: dict,          # {"ema_fast": [5,9,13], "ema_slow": [15,21,26]}
        metric: str = "sharpe",    # Optimize for Sharpe ratio
    ) -> OptimizationResult
```

**Performance metrics generated:**
```
═══════════════════════════════════════════
        BACKTEST REPORT — ORB Strategy
        Period: Jan 2025 — Jun 2025
═══════════════════════════════════════════

Return Metrics:
  Total Return:          18.4%
  CAGR:                  38.2%
  Max Drawdown:          -6.3%
  Calmar Ratio:          6.06

Trade Metrics:
  Total Trades:          287
  Win Rate:              58.2%
  Average Win:           ₹1,240
  Average Loss:          ₹780
  Profit Factor:         2.21
  Expectancy:            ₹312/trade

Risk Metrics:
  Sharpe Ratio:          2.14
  Sortino Ratio:         3.01
  Max Consecutive Losses: 5
  Average Holding Time:   47 mins

Charge Estimate:
  Total Brokerage:       ₹5,740
  STT + Charges:         ₹3,200
  Net After Charges:     ₹15,460
═══════════════════════════════════════════
```

**HTML report includes:**
- Equity curve chart
- Drawdown chart
- Monthly returns heatmap
- Win/loss distribution
- Trade-by-trade log
- Parameter sensitivity analysis

---

### 3.8 Dashboard (`dashboard/`)

**Terminal UI (primary — using Rich/Textual):**
```
╔══════════════════════════════════════════════════════════╗
║  AlgoTrader India | Mode: PAPER | Date: 2025-07-15     ║
╠══════════════════════════════════════════════════════════╣
║                                                          ║
║  Account Balance: ₹1,00,000  |  Used: ₹42,000 (42%)    ║
║  Day P&L: +₹1,240 (+1.24%)  |  Trades: 4/10            ║
║                                                          ║
║  ┌─ Active Positions ──────────────────────────────────┐ ║
║  │ RELIANCE  LONG  ₹2,450  Qty:18  P&L: +₹360  +0.8% │ ║
║  │ INFY      SHORT ₹1,580  Qty:30  P&L: -₹120  -0.3% │ ║
║  └─────────────────────────────────────────────────────┘ ║
║                                                          ║
║  ┌─ Today's Signals ──────────────────────────────────┐  ║
║  │ 09:31 ORB    RELIANCE  LONG   ₹2,440  ✓ Executed  │  ║
║  │ 09:45 VWAP   INFY      SHORT  ₹1,585  ✓ Executed  │  ║
║  │ 10:12 MOM    TATAMOT   LONG   ₹780    ✗ Risk Fail │  ║
║  │ 11:30 EMA    HDFCBANK  LONG   ₹1,620  ⏳ Pending  │  ║
║  └─────────────────────────────────────────────────────┘  ║
║                                                          ║
║  ┌─ Scanner Results (Today) ───────────────────────────┐ ║
║  │ RELIANCE  Score:87  Vol:2.1x  Gap:+1.2%  ATR:2.3%  │ ║
║  │ INFY      Score:82  Vol:1.8x  Gap:-0.8%  ATR:1.9%  │ ║
║  │ TATAMOT   Score:79  Vol:2.4x  Gap:+0.5%  ATR:2.8%  │ ║
║  └─────────────────────────────────────────────────────┘  ║
╚══════════════════════════════════════════════════════════╝
```

**Optional Web UI (FastAPI + HTMX):**
- Real-time position monitoring
- Trade history with charts
- Backtest runner from browser
- Strategy enable/disable toggles
- Risk parameter adjustment

---

## 4. Configuration System

### Master Config (`config/settings.yaml`)
```yaml
# ─── Broker ───
broker:
  name: "angelone"
  client_id: "${ANGEL_CLIENT_ID}"
  password: "${ANGEL_PASSWORD}"
  totp_secret: "${ANGEL_TOTP_SECRET}"
  api_key: "${ANGEL_API_KEY}"

# ─── Trading Mode ───
trading:
  mode: "paper"                  # "paper" or "live"
  paper_capital: 100000          # Virtual capital for paper mode

# ─── Risk Management ───
risk:
  max_capital_usage_percent: 60
  per_trade_risk_percent: 1.5
  max_position_size_percent: 15
  max_daily_loss_percent: 3.0
  max_daily_trades: 10
  max_open_positions: 5
  mandatory_stop_loss: true
  max_stop_loss_percent: 2.0
  trailing_stop_enabled: true
  trailing_stop_trigger_percent: 1.0
  trailing_stop_distance_percent: 0.5
  no_new_trades_after: "14:30"
  force_square_off_time: "15:10"
  no_trade_first_minutes: 3
  cooldown_after_consecutive_losses: 3
  cooldown_duration_minutes: 30

# ─── Scanner ───
scanner:
  min_volume: 500000
  min_turnover_cr: 10
  price_range: [100, 5000]
  min_atr_percent: 1.5
  max_stocks: 15
  scan_time: "08:50"
  sectors_exclude: ["REALTY"]    # Optional sector filters

# ─── Strategies ───
strategies:
  enabled:
    - orb
    - vwap_reversion
    - momentum_breakout
  
  orb:
    timeframe: "15m"
    opening_range_candles: 1
    volume_multiplier: 1.5
    min_body_ratio: 0.6
    risk_reward: 2.0
  
  vwap_reversion:
    timeframe: "5m"
    std_dev_bands: 1.0
    rsi_period: 14
    rsi_oversold: 35
    rsi_overbought: 65
  
  momentum_breakout:
    timeframe: "5m"
    consolidation_candles: 10
    volume_surge_multiplier: 2.0
    adx_threshold: 25
    ema_fast: 9
    ema_slow: 21

# ─── Backtesting ───
backtest:
  data_source: "angelone"        # "angelone", "yfinance", "tvdatafeed"
  default_period_days: 180
  slippage_percent: 0.05
  commission_per_order: 20
  cache_dir: "data/historical"

# ─── Scheduling ───
schedule:
  market_open: "09:15"
  market_close: "15:30"
  scanner_run: "08:50"
  pre_market_check: "09:00"
  square_off_check: "15:10"

# ─── Notifications ───
notifications:
  enabled: true
  telegram:
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    chat_id: "${TELEGRAM_CHAT_ID}"
  notify_on:
    - trade_entry
    - trade_exit
    - daily_summary
    - kill_switch_triggered
    - error
```

---

## 5. Build Phases

### Phase 1: Foundation (Week 1)
- [ ] Project setup, dependencies, config loader
- [ ] Angel One broker wrapper (auth, funds, orders)
- [ ] Historical data downloader + caching (Parquet)
- [ ] Base strategy class + signal/trade data models

### Phase 2: Backtesting Engine (Week 2)
- [ ] Backtest engine with trade simulation
- [ ] Slippage + commission modeling (Indian market charges)
- [ ] Performance metrics calculator
- [ ] HTML report generator with charts (Plotly)
- [ ] ORB strategy implementation + first backtest

### Phase 3: All Strategies + Optimization (Week 3)
- [ ] VWAP Reversion strategy
- [ ] Momentum Breakout strategy
- [ ] EMA Crossover strategy
- [ ] Gap Fill strategy
- [ ] Parameter optimizer (grid search)
- [ ] Strategy comparison dashboard

### Phase 4: Stock Scanner (Week 4)
- [ ] Pre-market scanner with all filters
- [ ] Sector rotation analysis
- [ ] Daily stock ranking system
- [ ] Scanner backtest (verify picks historically)

### Phase 5: Paper Trading (Week 5)
- [ ] Paper trading engine with live tick data
- [ ] Real-time position tracking
- [ ] SQLite trade journal
- [ ] Terminal dashboard (Rich/Textual)
- [ ] Risk manager integration

### Phase 6: Live Trading (Week 6)
- [ ] Live execution engine
- [ ] Order monitoring + exit management
- [ ] Kill switches + safety checks
- [ ] Telegram notifications
- [ ] Error handling + recovery

### Phase 7: Monitoring + VPS (Week 7)
- [ ] Web dashboard (FastAPI + HTMX)
- [ ] VPS deployment scripts
- [ ] Cron scheduling for auto-start
- [ ] Log rotation + monitoring
- [ ] Health checks + auto-restart

---

## 6. Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Language | Python 3.11+ | Best algo trading ecosystem |
| Broker API | smartapi-python | Angel One official SDK |
| Data Processing | pandas, numpy | Industry standard |
| Backtesting | vectorbt + custom | Fast vectorized backtesting |
| Technical Analysis | pandas-ta | 130+ indicators |
| Scheduling | APScheduler | Robust job scheduling |
| Database | SQLite | Zero setup, local-first |
| Data Storage | Parquet | Fast columnar storage for OHLCV |
| Terminal UI | Rich + Textual | Beautiful terminal dashboards |
| Web UI | FastAPI + HTMX | Lightweight, real-time updates |
| Charts | Plotly | Interactive HTML charts |
| Notifications | python-telegram-bot | Free instant alerts |
| Config | PyYAML + pydantic | Validated configuration |
| Auth | pyotp | TOTP generation for Angel One |
| Async | asyncio + aiohttp | Non-blocking I/O |

---

## 7. Indian Market Charges (Important for Realistic Backtesting)

```
Angel One Intraday Charges:
├── Brokerage:         ₹20/order (flat) or 0.03% (whichever is lower)
├── STT (Sell side):   0.025% on sell turnover
├── Exchange Txn:      0.00345% (NSE)
├── SEBI Charges:      0.0001%
├── GST:               18% on (brokerage + exchange txn + SEBI)
├── Stamp Duty:        0.003% on buy side
└── Total Estimate:    ~0.05% per round trip (buy + sell)

For ₹50,000 trade:
  Entry cost:  ~₹25
  Exit cost:   ~₹25
  Total:       ~₹50 per round trip
```

These charges are modeled in the backtester for realistic P&L.

---

## 8. Deployment

### Local Setup
```bash
# Clone + setup
git clone <repo>
cd algotrader-india
python -m venv venv
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with Angel One credentials
# Edit config/settings.yaml with risk parameters

# Run backtest first
python scripts/run_backtest.py --strategy orb --days 180

# Run paper trading
python scripts/run_bot.py --mode paper

# When confident, go live
python scripts/run_bot.py --mode live
```

### VPS Deployment (Later)
```bash
# Recommended: Oracle Cloud Free Tier (truly free)
# or DigitalOcean ₹500/month basic droplet

# Systemd service for auto-start
sudo systemctl enable algotrader
sudo systemctl start algotrader

# Cron for daily schedule
# 08:45 - Start bot
# 15:35 - Stop bot + generate report
# 20:00 - Download next day data
```

---

## 9. Safety Rules (Non-Negotiable)

1. **NEVER trade without a stop loss** — every single order must have SL
2. **Paper trade for minimum 2 weeks** before going live
3. **Start live with 25% of intended capital** — scale up after consistent profits
4. **Daily loss limit is sacred** — if 3% daily loss, bot shuts down, no overrides
5. **No trading in first 3 minutes** — 9:15-9:18 is pure noise
6. **Force square-off by 3:10 PM** — never carry intraday positions
7. **Backtest every strategy** for minimum 6 months data before deploying
8. **Log everything** — every signal, every order, every rejection reason
9. **Monitor the bot** — automation ≠ set and forget
10. **Keep credentials secure** — .env files, never commit to git

---

## 10. What This Architecture Does NOT Include (Out of Scope for v1)

- Options trading / F&O strategies (can be added as v2)
- Machine learning / AI-based prediction models
- Multi-broker support (focused on Angel One)
- Social sentiment analysis
- News-based trading
- Arbitrage strategies
- HFT (High Frequency Trading — needs co-location)

These can be layered on top of this architecture in future versions.

---

## Next Steps

1. **Confirm this plan** — adjust any parameters or strategies
2. **Start Phase 1** — foundation + broker module
3. **Build Phase 2** — backtesting engine (most important for validation)
4. **Iterate** — test strategies, optimize, paper trade, then go live

---

*Generated for Arshad | AlgoTrader India v1.0 | March 2026*
