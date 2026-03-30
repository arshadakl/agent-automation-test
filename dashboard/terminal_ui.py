"""Rich/Textual terminal dashboard for live trading."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from loguru import logger

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.live import Live
    from rich.text import Text
    from rich import box

    _RICH_AVAILABLE = True
except ImportError:
    _RICH_AVAILABLE = False
    logger.warning("rich not installed; terminal UI disabled.")


class TerminalUI:
    """Live terminal dashboard using Rich.

    Shows:
    - Portfolio summary (capital, P&L, drawdown)
    - Open positions table
    - Recent signals
    - Kill switch / market status
    - Keyboard shortcut hints
    """

    REFRESH_RATE = 2  # seconds

    def __init__(
        self,
        portfolio: "Portfolio",  # noqa: F821
        risk_manager: "RiskManager",  # noqa: F821
        execution_engine: "ExecutionEngine",  # noqa: F821
        mode: str = "paper",
    ) -> None:
        self._portfolio = portfolio
        self._risk = risk_manager
        self._execution = execution_engine
        self._mode = mode
        self._recent_signals: list[dict[str, Any]] = []
        self._running = False
        self._console = Console() if _RICH_AVAILABLE else None

    def add_signal(self, signal_dict: dict[str, Any]) -> None:
        """Push a new signal to the recent-signals list (max 10)."""
        self._recent_signals.insert(0, signal_dict)
        self._recent_signals = self._recent_signals[:10]

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _build_layout(self) -> "Layout":
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body"),
            Layout(name="footer", size=3),
        )
        layout["body"].split_row(
            Layout(name="left"),
            Layout(name="right"),
        )
        layout["left"].split_column(
            Layout(name="portfolio", ratio=2),
            Layout(name="positions"),
        )
        layout["right"].split_column(
            Layout(name="signals"),
            Layout(name="status"),
        )
        return layout

    def _render_header(self) -> "Panel":
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        mode_color = "yellow" if self._mode == "paper" else "green"
        text = Text(f" AlgoTrader India  |  {now}  |  Mode: ", style="bold")
        text.append(self._mode.upper(), style=f"bold {mode_color}")
        return Panel(text, box=box.DOUBLE)

    def _render_portfolio(self) -> "Panel":
        pd = self._portfolio.to_dict()
        table = Table(show_header=False, box=box.SIMPLE)
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="white")
        table.add_row("Capital (₹)", f"{pd['available_capital']:,.0f}")
        table.add_row("Total Value (₹)", f"{pd['total_value']:,.0f}")
        table.add_row("Realised P&L (₹)", _pnl_color(pd["realised_pnl"]))
        table.add_row("Unrealised P&L (₹)", _pnl_color(pd["unrealised_pnl"]))
        table.add_row("Drawdown (%)", f"[red]{pd['drawdown_pct']:.2f}%[/red]")
        table.add_row("Trades Today", str(self._risk.daily_trade_count))
        return Panel(table, title="[bold]Portfolio[/bold]", box=box.ROUNDED)

    def _render_positions(self) -> "Panel":
        table = Table(show_header=True, box=box.SIMPLE)
        table.add_column("Symbol")
        table.add_column("Dir")
        table.add_column("Qty")
        table.add_column("Entry")
        table.add_column("LTP")
        table.add_column("P&L")

        for pos in self._portfolio.open_positions.values():
            pnl = pos.unrealised_pnl
            color = "green" if pnl >= 0 else "red"
            table.add_row(
                pos.symbol,
                f"[cyan]{pos.direction}[/cyan]",
                str(pos.quantity),
                f"{pos.entry_price:.2f}",
                f"{pos.current_price:.2f}",
                f"[{color}]{pnl:+.0f}[/{color}]",
            )

        return Panel(table, title="[bold]Open Positions[/bold]", box=box.ROUNDED)

    def _render_signals(self) -> "Panel":
        table = Table(show_header=True, box=box.SIMPLE)
        table.add_column("Time")
        table.add_column("Symbol")
        table.add_column("Strategy")
        table.add_column("Dir")
        table.add_column("Entry")

        for sig in self._recent_signals[:8]:
            color = "green" if sig.get("direction") == "LONG" else "red"
            table.add_row(
                sig.get("time", ""),
                sig.get("symbol", ""),
                sig.get("strategy", ""),
                f"[{color}]{sig.get('direction', '')}[/{color}]",
                str(sig.get("entry_price", "")),
            )

        return Panel(table, title="[bold]Recent Signals[/bold]", box=box.ROUNDED)

    def _render_status(self) -> "Panel":
        kill = self._risk.check_kill_switches()
        sq_off = self._risk.should_square_off()
        table = Table(show_header=False, box=box.SIMPLE)
        table.add_column("Key", style="cyan")
        table.add_column("Value")
        table.add_row("Kill Switch", f"[red]{kill}[/red]" if kill else "[green]INACTIVE[/green]")
        table.add_row("Square Off", f"[red]YES[/red]" if sq_off else "[green]NO[/green]")
        table.add_row("Consecutive Losses", str(self._risk.consecutive_losses))
        return Panel(table, title="[bold]Status[/bold]", box=box.ROUNDED)

    def _render_footer(self) -> "Panel":
        return Panel(
            Text("[Q] Quit  [P] Pause  [S] Square-Off All", style="dim"),
            box=box.SIMPLE,
        )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Start the live terminal dashboard."""
        if not _RICH_AVAILABLE:
            logger.warning("TerminalUI: rich not installed.")
            return

        self._running = True
        layout = self._build_layout()

        with Live(layout, console=self._console, refresh_per_second=1 / self.REFRESH_RATE, screen=True):
            while self._running:
                layout["header"].update(self._render_header())
                layout["portfolio"].update(self._render_portfolio())
                layout["positions"].update(self._render_positions())
                layout["signals"].update(self._render_signals())
                layout["status"].update(self._render_status())
                layout["footer"].update(self._render_footer())
                await asyncio.sleep(self.REFRESH_RATE)

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _pnl_color(value: float) -> str:
    color = "green" if value >= 0 else "red"
    return f"[{color}]{value:+,.0f}[/{color}]"
