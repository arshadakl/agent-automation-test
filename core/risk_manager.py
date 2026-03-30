"""Risk manager – position sizing, kill switches, time rules."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from loguru import logger

from strategies.base_strategy import Signal


class RiskManager:
    """Validates signals and enforces all risk rules.

    Kill switches:
    - Daily loss > ``max_daily_loss_percent`` → stop all new trades
    - ``cooldown_after_consecutive_losses`` consecutive losses → pause for
      ``cooldown_duration_minutes`` minutes
    - Drawdown > 5% → reduce position sizes by 50%
    - More than ``max_daily_trades`` today → stop

    Time rules:
    - No new trades before 09:15 + ``no_trade_first_minutes``
    - No new trades after ``no_new_trades_after``
    - Force square-off at ``force_square_off_time``
    """

    def __init__(self, config: dict[str, Any], initial_capital: float) -> None:
        self._cfg = config
        self._initial_capital = initial_capital

        # State
        self._daily_pnl: float = 0.0
        self._daily_trade_count: int = 0
        self._consecutive_losses: int = 0
        self._cooldown_until: datetime | None = None
        self._kill_reason: str | None = None
        self._peak_capital: float = initial_capital

    # ------------------------------------------------------------------
    # Signal validation
    # ------------------------------------------------------------------

    def validate_signal(self, signal: Signal, portfolio: "Portfolio") -> bool:  # noqa: F821
        """Return True only if the signal passes all risk checks."""
        now = datetime.now()

        # Kill switch check
        kill = self.check_kill_switches()
        if kill:
            logger.debug(f"RiskManager.validate_signal: blocked by kill switch – {kill}")
            return False

        # Time rules
        if not self._is_trading_time(now):
            return False

        # Max daily trades
        if self._daily_trade_count >= self._cfg.get("max_daily_trades", 10):
            logger.debug("RiskManager: max daily trades reached.")
            return False

        # Max open positions
        if len(portfolio.open_positions) >= self._cfg.get("max_open_positions", 5):
            logger.debug("RiskManager: max open positions reached.")
            return False

        # Duplicate symbol check
        if signal.symbol in portfolio.open_positions:
            logger.debug(f"RiskManager: already have open position in {signal.symbol}.")
            return False

        # SL sanity
        if self._cfg.get("mandatory_stop_loss", True):
            if signal.direction == "LONG" and signal.stop_loss >= signal.entry_price:
                logger.debug("RiskManager: SL above entry for LONG – rejected.")
                return False
            if signal.direction == "SHORT" and signal.stop_loss <= signal.entry_price:
                logger.debug("RiskManager: SL below entry for SHORT – rejected.")
                return False

        # Max SL percent
        max_sl_pct = self._cfg.get("max_stop_loss_percent", 2.0)
        sl_pct = abs(signal.entry_price - signal.stop_loss) / signal.entry_price * 100
        if sl_pct > max_sl_pct:
            logger.debug(f"RiskManager: SL too wide ({sl_pct:.2f}% > {max_sl_pct}%).")
            return False

        return True

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------

    def calculate_position_size(
        self,
        capital: float,
        entry_price: float,
        stop_loss: float,
    ) -> int:
        """Return number of shares to trade.

        Formula: risk_amount / sl_distance, capped at max_position_size.
        """
        risk_pct = self._cfg.get("per_trade_risk_percent", 1.5) / 100
        max_pos_pct = self._cfg.get("max_position_size_percent", 15) / 100

        risk_amount = capital * risk_pct * self.get_size_multiplier()
        sl_distance = abs(entry_price - stop_loss)
        if sl_distance == 0:
            return 0

        qty_by_risk = int(risk_amount / sl_distance)
        max_qty_by_capital = int((capital * max_pos_pct) / entry_price)

        qty = min(qty_by_risk, max_qty_by_capital)
        return max(qty, 0)

    # ------------------------------------------------------------------
    # Kill switches
    # ------------------------------------------------------------------

    def check_kill_switches(self) -> str | None:
        """Return a reason string if any kill switch is active, else None."""
        now = datetime.now()

        # Previously set hard kill
        if self._kill_reason:
            return self._kill_reason

        # Daily loss limit
        if self._initial_capital > 0:
            daily_loss_pct = (-self._daily_pnl / self._initial_capital) * 100
            max_loss = self._cfg.get("max_daily_loss_percent", 3.0)
            if daily_loss_pct >= max_loss:
                reason = f"Daily loss limit reached ({daily_loss_pct:.2f}%)"
                self._kill_reason = reason
                logger.warning(f"KILL SWITCH: {reason}")
                return reason

        # Cooldown
        if self._cooldown_until and now < self._cooldown_until:
            remaining = int((self._cooldown_until - now).total_seconds() / 60)
            return f"Cooldown active – {remaining}m remaining"

        return None

    def record_trade_result(self, pnl: float) -> None:
        """Update state after a trade closes."""
        self._daily_pnl += pnl
        self._daily_trade_count += 1

        if pnl < 0:
            self._consecutive_losses += 1
            cooldown_threshold = self._cfg.get("cooldown_after_consecutive_losses", 3)
            cooldown_mins = self._cfg.get("cooldown_duration_minutes", 30)
            if self._consecutive_losses >= cooldown_threshold:
                self._cooldown_until = datetime.now() + timedelta(minutes=cooldown_mins)
                logger.warning(
                    f"RiskManager: {self._consecutive_losses} consecutive losses – "
                    f"cooldown until {self._cooldown_until.strftime('%H:%M')}"
                )
                self._consecutive_losses = 0
        else:
            self._consecutive_losses = 0

    def update_daily_pnl(self, pnl: float) -> None:
        """Directly update the running daily P&L (e.g., from unrealised MTM)."""
        self._daily_pnl = pnl

    def reset_daily(self) -> None:
        """Reset all daily counters (call at start of each trading day)."""
        self._daily_pnl = 0.0
        self._daily_trade_count = 0
        self._consecutive_losses = 0
        self._cooldown_until = None
        self._kill_reason = None
        logger.info("RiskManager: daily state reset.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def should_square_off(self) -> bool:
        """Return True if it is past the force square-off time."""
        sq_time_str = self._cfg.get("force_square_off_time", "15:10")
        sq_h, sq_m = map(int, sq_time_str.split(":"))
        now = datetime.now().time()
        return now >= time(sq_h, sq_m)

    def get_size_multiplier(self) -> float:
        """Return 0.5 if current drawdown > 5%, else 1.0."""
        drawdown_pct = (-self._daily_pnl / self._initial_capital) * 100 if self._initial_capital else 0
        return 0.5 if drawdown_pct > 5.0 else 1.0

    def _is_trading_time(self, now: datetime) -> bool:
        """Check if *now* is within allowed trading hours."""
        current_time = now.time()

        # No trading before market open + buffer
        no_trade_minutes = self._cfg.get("no_trade_first_minutes", 3)
        earliest = time(9, 15 + no_trade_minutes)
        if current_time < earliest:
            logger.debug(f"RiskManager: too early to trade ({current_time}).")
            return False

        # No new trades after cutoff
        cutoff_str = self._cfg.get("no_new_trades_after", "14:30")
        ch, cm = map(int, cutoff_str.split(":"))
        if current_time >= time(ch, cm):
            logger.debug(f"RiskManager: past no-new-trades cutoff ({cutoff_str}).")
            return False

        return True

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def daily_trade_count(self) -> int:
        return self._daily_trade_count

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_losses
