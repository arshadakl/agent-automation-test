"""FastAPI + HTMX web dashboard for AlgoTrader India."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any

from loguru import logger

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse
    import uvicorn

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False
    logger.warning("fastapi/uvicorn not installed; web UI disabled.")

try:
    import plotly.graph_objects as go

    _PLOTLY_AVAILABLE = True
except ImportError:
    _PLOTLY_AVAILABLE = False


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>AlgoTrader India</title>
<script src="https://unpkg.com/htmx.org@1.9.4"></script>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
  body {{ font-family: Arial, sans-serif; background: #0f0f0f; color: #e0e0e0; margin: 0; padding: 10px; }}
  h1 {{ color: #00bfff; }} h2 {{ color: #87ceeb; border-bottom: 1px solid #333; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }}
  .card {{ background: #1a1a2e; border-radius: 8px; padding: 14px; }}
  .pnl-pos {{ color: #00e676; }} .pnl-neg {{ color: #ff5252; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ border: 1px solid #333; padding: 6px 10px; text-align: left; }}
  th {{ background: #16213e; }}
  .btn {{ background: #e63946; color: white; border: none; padding: 8px 16px;
          border-radius: 4px; cursor: pointer; margin: 4px; }}
  .btn-blue {{ background: #1d3557; }}
</style>
</head>
<body>
<h1>🚀 AlgoTrader India Dashboard</h1>

<div class="grid">
  <div class="card">
    <h2>Portfolio</h2>
    <div id="pnl-panel" hx-get="/api/pnl" hx-trigger="every 3s" hx-swap="innerHTML">Loading…</div>
  </div>
  <div class="card">
    <h2>Open Positions</h2>
    <div id="positions-panel" hx-get="/api/positions" hx-trigger="every 3s" hx-swap="innerHTML">Loading…</div>
  </div>
  <div class="card">
    <h2>Recent Signals</h2>
    <div id="signals-panel" hx-get="/api/signals" hx-trigger="every 5s" hx-swap="innerHTML">Loading…</div>
  </div>
  <div class="card">
    <h2>Recent Trades</h2>
    <div id="trades-panel" hx-get="/api/trades" hx-trigger="every 5s" hx-swap="innerHTML">Loading…</div>
  </div>
</div>

<div class="card" style="margin-top:12px">
  <h2>Controls</h2>
  <button class="btn" hx-post="/api/pause" hx-swap="none">⏸ Pause Trading</button>
  <button class="btn btn-blue" hx-post="/api/resume" hx-swap="none">▶ Resume Trading</button>
</div>

<div class="card" style="margin-top:12px">
  <h2>Chart</h2>
  <input id="chart-symbol" type="text" placeholder="RELIANCE" style="padding:6px; width:150px; background:#333; color:#fff; border:1px solid #555">
  <button class="btn btn-blue" onclick="loadChart()">Load Chart</button>
  <div id="chart-container" style="height:400px"></div>
</div>

<script>
  // WebSocket live updates
  const ws = new WebSocket("ws://" + location.host + "/ws");
  ws.onmessage = (event) => {{
    const data = JSON.parse(event.data);
    if (data.type === "pnl") document.getElementById("pnl-panel").innerHTML = data.html;
    if (data.type === "positions") document.getElementById("positions-panel").innerHTML = data.html;
  }};

  function loadChart() {{
    const symbol = document.getElementById("chart-symbol").value || "RELIANCE";
    fetch("/api/chart/" + symbol)
      .then(r => r.json())
      .then(fig => Plotly.newPlot("chart-container", fig.data, fig.layout));
  }}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    portfolio: "Portfolio",  # noqa: F821
    risk_manager: "RiskManager",  # noqa: F821
    paper_trader: "PaperTrader | None" = None,  # noqa: F821
    data_feed: "DataFeed | None" = None,  # noqa: F821
) -> "FastAPI":
    """Create and configure the FastAPI application."""
    if not _FASTAPI_AVAILABLE:
        raise ImportError("fastapi is required for the web dashboard.")

    app = FastAPI(title="AlgoTrader India", version="1.0.0")
    _recent_signals: list[dict[str, Any]] = []
    _paused = [False]
    _ws_clients: list[WebSocket] = []

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(_HTML_TEMPLATE)

    @app.get("/api/positions")
    async def get_positions() -> HTMLResponse:
        pos_data = portfolio.to_dict()["open_positions"]
        if not pos_data:
            return HTMLResponse("<p style='color:#888'>No open positions.</p>")
        rows = "".join(
            f"<tr><td>{p['symbol']}</td><td>{p['direction']}</td>"
            f"<td>{p['quantity']}</td><td>{p['entry_price']:.2f}</td>"
            f"<td>{p['current_price']:.2f}</td>"
            f"<td class='{'pnl-pos' if p['unrealised_pnl'] >= 0 else 'pnl-neg'}'>{p['unrealised_pnl']:+.0f}</td></tr>"
            for p in pos_data
        )
        return HTMLResponse(
            f"<table><tr><th>Symbol</th><th>Dir</th><th>Qty</th><th>Entry</th><th>LTP</th><th>P&L</th></tr>{rows}</table>"
        )

    @app.get("/api/pnl")
    async def get_pnl() -> HTMLResponse:
        pd_data = portfolio.to_dict()
        cls_r = "pnl-pos" if pd_data["realised_pnl"] >= 0 else "pnl-neg"
        cls_u = "pnl-pos" if pd_data["unrealised_pnl"] >= 0 else "pnl-neg"
        html = (
            f"<p>Capital: ₹{pd_data['available_capital']:,.0f}</p>"
            f"<p>Total Value: ₹{pd_data['total_value']:,.0f}</p>"
            f"<p>Realised P&L: <span class='{cls_r}'>₹{pd_data['realised_pnl']:+,.0f}</span></p>"
            f"<p>Unrealised P&L: <span class='{cls_u}'>₹{pd_data['unrealised_pnl']:+,.0f}</span></p>"
            f"<p>Drawdown: <span class='pnl-neg'>{pd_data['drawdown_pct']:.2f}%</span></p>"
            f"<p>Trades Today: {risk_manager.daily_trade_count}</p>"
        )
        return HTMLResponse(html)

    @app.get("/api/trades")
    async def get_trades() -> HTMLResponse:
        trades = paper_trader.get_today_trades() if paper_trader else []
        closed = [t for t in trades if t.get("status") == "CLOSED"][-10:]
        if not closed:
            return HTMLResponse("<p style='color:#888'>No trades today.</p>")
        rows = "".join(
            f"<tr><td>{t['symbol']}</td><td>{t['direction']}</td>"
            f"<td>{t.get('entry_price', 0):.2f}</td><td>{t.get('exit_price', 0):.2f}</td>"
            f"<td class='{'pnl-pos' if (t.get('pnl') or 0) >= 0 else 'pnl-neg'}'>{(t.get('pnl') or 0):+.0f}</td>"
            f"<td>{t.get('exit_reason', '')}</td></tr>"
            for t in reversed(closed)
        )
        return HTMLResponse(
            f"<table><tr><th>Symbol</th><th>Dir</th><th>Entry</th><th>Exit</th><th>P&L</th><th>Reason</th></tr>{rows}</table>"
        )

    @app.get("/api/signals")
    async def get_signals() -> JSONResponse:
        return JSONResponse({"signals": _recent_signals[-10:]})

    @app.post("/api/pause")
    async def pause_trading() -> JSONResponse:
        _paused[0] = True
        logger.warning("WebUI: trading paused by user.")
        return JSONResponse({"status": "paused"})

    @app.post("/api/resume")
    async def resume_trading() -> JSONResponse:
        _paused[0] = False
        logger.info("WebUI: trading resumed by user.")
        return JSONResponse({"status": "resumed"})

    @app.get("/api/chart/{symbol}")
    async def get_chart(symbol: str) -> JSONResponse:
        if not _PLOTLY_AVAILABLE or data_feed is None:
            return JSONResponse({"data": [], "layout": {"title": "Plotly not available"}})

        df = data_feed.get_candles(symbol, "5m")
        if df.empty:
            return JSONResponse({"data": [], "layout": {"title": f"No data for {symbol}"}})

        fig = go.Figure(data=[
            go.Candlestick(
                x=df["datetime"],
                open=df["open"], high=df["high"],
                low=df["low"], close=df["close"],
                name=symbol,
            )
        ])
        fig.update_layout(
            title=f"{symbol} – 5m",
            xaxis_rangeslider_visible=False,
            template="plotly_dark",
        )
        return JSONResponse(json.loads(fig.to_json()))

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(ws_client: WebSocket) -> None:
        await ws_client.accept()
        _ws_clients.append(ws_client)
        try:
            while True:
                await asyncio.sleep(3)
                pd_data = portfolio.to_dict()
                msg = json.dumps({"type": "pnl", "data": pd_data})
                await ws_client.send_text(msg)
        except WebSocketDisconnect:
            _ws_clients.remove(ws_client)
        except Exception as exc:
            logger.error(f"WebSocket error: {exc}")
            if ws_client in _ws_clients:
                _ws_clients.remove(ws_client)

    # Expose for signal injection
    app.state.recent_signals = _recent_signals
    app.state.paused = _paused
    return app


def run_server(app: "FastAPI", host: str = "0.0.0.0", port: int = 8000) -> None:
    """Start the Uvicorn server (blocking)."""
    uvicorn.run(app, host=host, port=port, log_level="warning")
