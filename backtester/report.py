"""Backtest report generator – metrics, equity curve, HTML/CSV export."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from backtester.engine import BacktestResult, TradeRecord


class BacktestReport:
    """Generate performance reports from BacktestResult objects.

    Outputs:
    - Console summary (Rich table)
    - HTML report with Plotly equity curve and monthly returns
    - CSV trade log
    """

    def __init__(self, result: BacktestResult) -> None:
        self._r = result

    # ------------------------------------------------------------------
    # Console summary
    # ------------------------------------------------------------------

    def print_summary(self) -> None:
        """Print a formatted summary to stdout."""
        try:
            from rich.console import Console
            from rich.table import Table

            console = Console()
            table = Table(title=f"Backtest: {self._r.strategy_name} | {self._r.symbol}", show_lines=True)
            table.add_column("Metric", style="bold cyan")
            table.add_column("Value", style="bold white")

            rows = self._metrics_rows()
            for k, v in rows.items():
                table.add_row(k, str(v))

            console.print(table)
        except ImportError:
            for k, v in self._metrics_rows().items():
                print(f"{k:35s}: {v}")

    # ------------------------------------------------------------------
    # HTML export
    # ------------------------------------------------------------------

    def to_html(self, output_path: str = "data/reports/backtest_report.html") -> str:
        """Generate an HTML report and save to *output_path*.  Returns the path."""
        try:
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots
        except ImportError:
            logger.warning("BacktestReport: plotly not installed; skipping HTML report.")
            return ""

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        fig = make_subplots(
            rows=2, cols=1,
            subplot_titles=("Equity Curve", "Drawdown (%)"),
            vertical_spacing=0.12,
            row_heights=[0.7, 0.3],
        )

        equity = self._r.equity_curve
        if not equity.empty:
            fig.add_trace(
                go.Scatter(y=equity.values, mode="lines", name="Equity", line=dict(color="royalblue")),
                row=1, col=1,
            )
            peak = equity.cummax()
            drawdown = (peak - equity) / peak * 100
            fig.add_trace(
                go.Scatter(y=drawdown.values, mode="lines", fill="tozeroy", name="Drawdown", line=dict(color="crimson")),
                row=2, col=1,
            )

        metrics = self._metrics_rows()
        metrics_html = "<table border='1' cellpadding='6' style='border-collapse:collapse;font-family:monospace'>"
        for k, v in metrics.items():
            metrics_html += f"<tr><td><b>{k}</b></td><td>{v}</td></tr>"
        metrics_html += "</table>"

        monthly_html = self._monthly_returns_html()
        trades_html = self._trades_html()

        html = f"""<!DOCTYPE html>
<html>
<head><title>Backtest Report – {self._r.strategy_name}</title>
<style>body{{font-family:Arial,sans-serif;margin:20px}} h2{{color:#333}}</style>
</head>
<body>
<h2>Backtest Report: {self._r.strategy_name} | {self._r.symbol}</h2>
<p>Period: {self._r.start_date} → {self._r.end_date}</p>
<h3>Performance Metrics</h3>
{metrics_html}
<h3>Equity Curve & Drawdown</h3>
{fig.to_html(full_html=False, include_plotlyjs='cdn')}
<h3>Monthly Returns</h3>
{monthly_html}
<h3>Trade Log</h3>
{trades_html}
</body></html>"""

        with open(output_path, "w") as fh:
            fh.write(html)
        logger.info(f"BacktestReport: HTML saved to {output_path}")
        return output_path

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    def to_csv(self, output_path: str = "data/reports/trades.csv") -> str:
        """Save the trade log to CSV."""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        df = self._trades_df()
        df.to_csv(output_path, index=False)
        logger.info(f"BacktestReport: trade CSV saved to {output_path}")
        return output_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _metrics_rows(self) -> dict[str, Any]:
        r = self._r
        return {
            "Strategy": r.strategy_name,
            "Symbol": r.symbol,
            "Period": f"{r.start_date} → {r.end_date}",
            "Initial Capital (₹)": f"{r.initial_capital:,.0f}",
            "Total Trades": r.total_trades,
            "Win Rate (%)": f"{r.win_rate:.1f}",
            "Profit Factor": f"{r.profit_factor:.2f}",
            "Total Return (%)": f"{r.total_return_pct:.2f}",
            "CAGR (%)": f"{r.cagr:.2f}",
            "Sharpe Ratio": f"{r.sharpe_ratio:.2f}",
            "Max Drawdown (%)": f"{r.max_drawdown_pct:.2f}",
            "Avg P&L per Trade (₹)": f"{r.avg_pnl:.2f}",
        }

    def _trades_df(self) -> pd.DataFrame:
        if not self._r.trades:
            return pd.DataFrame()
        return pd.DataFrame([
            {
                "symbol": t.symbol,
                "strategy": t.strategy,
                "direction": t.direction,
                "entry_date": t.entry_date,
                "exit_date": t.exit_date,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "quantity": t.quantity,
                "pnl_gross": t.pnl_gross,
                "charges": t.charges,
                "pnl_net": t.pnl_net,
                "exit_reason": t.exit_reason,
                "holding_bars": t.holding_bars,
            }
            for t in self._r.trades
        ])

    def _trades_html(self) -> str:
        df = self._trades_df()
        if df.empty:
            return "<p>No trades.</p>"
        return df.to_html(index=False, border=1, classes="trade-table")

    def _monthly_returns_html(self) -> str:
        """Build a month × year returns table."""
        trades = self._r.trades
        if not trades:
            return "<p>No trades to compute monthly returns.</p>"

        rows = []
        for t in trades:
            if t.entry_date:
                rows.append({"month": pd.Timestamp(t.entry_date).to_period("M"), "pnl": t.pnl_net})

        if not rows:
            return "<p>No date data available.</p>"

        df = pd.DataFrame(rows).groupby("month")["pnl"].sum().reset_index()
        df["year"] = df["month"].apply(lambda x: x.year)
        df["mon"] = df["month"].apply(lambda x: x.strftime("%b"))
        pivot = df.pivot_table(index="year", columns="mon", values="pnl", aggfunc="sum")
        return pivot.fillna(0).to_html(classes="monthly-table", border=1, float_format="{:.0f}".format)
