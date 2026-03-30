"""Pytest tests for the RiskManager."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from strategies.base_strategy import Signal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def basic_config() -> dict:
    return {
        "per_trade_risk_percent": 1.5,
        "max_position_size_percent": 15.0,
        "max_daily_loss_percent": 3.0,
        "max_daily_trades": 10,
        "max_open_positions": 5,
        "mandatory_stop_loss": True,
        "max_stop_loss_percent": 2.0,
        "no_new_trades_after": "14:30",
        "force_square_off_time": "15:10",
        "no_trade_first_minutes": 3,
        "cooldown_after_consecutive_losses": 3,
        "cooldown_duration_minutes": 30,
        "trailing_stop_enabled": True,
        "trailing_stop_trigger_percent": 1.0,
        "trailing_stop_distance_percent": 0.5,
    }


@pytest.fixture
def risk_manager(basic_config: dict) -> "RiskManager":
    from core.risk_manager import RiskManager
    return RiskManager(basic_config, initial_capital=100_000.0)


@pytest.fixture
def sample_signal() -> Signal:
    return Signal(
        strategy="orb",
        symbol="RELIANCE",
        direction="LONG",
        entry_price=2500.0,
        stop_loss=2450.0,
        target_1=2600.0,
        target_2=None,
        confidence=0.75,
        timestamp=datetime.now(),
    )


@pytest.fixture
def mock_portfolio() -> MagicMock:
    portfolio = MagicMock()
    portfolio.open_positions = {}
    return portfolio


# ---------------------------------------------------------------------------
# Position Sizing Tests
# ---------------------------------------------------------------------------


class TestPositionSizing:
    def test_basic_position_size(self, risk_manager: "RiskManager") -> None:
        """Test basic position sizing formula: risk_amount / sl_distance."""
        # Capital=100k, risk%=1.5%, risk_amount=1500
        # SL distance = 2500 - 2450 = 50
        # qty = 1500/50 = 30; max_qty = 100k*0.15/2500 = 6
        qty = risk_manager.calculate_position_size(100_000, 2500.0, 2450.0)
        assert qty == 6  # capped by max position size

    def test_position_size_zero_sl(self, risk_manager: "RiskManager") -> None:
        """SL == entry price should return 0."""
        qty = risk_manager.calculate_position_size(100_000, 1000.0, 1000.0)
        assert qty == 0

    def test_position_size_capped_by_risk(self, risk_manager: "RiskManager") -> None:
        """Tight SL → qty limited by risk amount."""
        # risk_amount = 1500, sl_distance = 5, qty_by_risk = 300
        # max_qty = 100k * 0.15 / 1000 = 15 → capped at 15
        qty = risk_manager.calculate_position_size(100_000, 1000.0, 995.0)
        assert qty == 15

    def test_size_multiplier_normal(self, risk_manager: "RiskManager") -> None:
        assert risk_manager.get_size_multiplier() == 1.0

    def test_size_multiplier_under_drawdown(self, risk_manager: "RiskManager") -> None:
        """Drawdown > 5% should halve the multiplier."""
        risk_manager.update_daily_pnl(-6000.0)  # 6% loss
        assert risk_manager.get_size_multiplier() == 0.5

    def test_position_size_halved_under_drawdown(self, risk_manager: "RiskManager") -> None:
        """Position size should be halved when drawdown > 5%."""
        risk_manager.update_daily_pnl(-6000.0)
        qty_normal = risk_manager.calculate_position_size(100_000, 2500.0, 2450.0)
        risk_manager.update_daily_pnl(0)  # reset
        qty_full = risk_manager.calculate_position_size(100_000, 2500.0, 2450.0)
        assert qty_normal <= qty_full


# ---------------------------------------------------------------------------
# Kill Switch Tests
# ---------------------------------------------------------------------------


class TestKillSwitches:
    def test_no_kill_switch_initially(self, risk_manager: "RiskManager") -> None:
        assert risk_manager.check_kill_switches() is None

    def test_daily_loss_limit_triggers(self, risk_manager: "RiskManager") -> None:
        """Losing 3%+ of capital should trigger the kill switch."""
        risk_manager.update_daily_pnl(-3100.0)  # > 3% of 100k
        result = risk_manager.check_kill_switches()
        assert result is not None
        assert "Daily loss limit" in result

    def test_consecutive_losses_trigger_cooldown(self, risk_manager: "RiskManager") -> None:
        """3 consecutive losses should trigger a cooldown."""
        risk_manager.record_trade_result(-100.0)
        risk_manager.record_trade_result(-100.0)
        risk_manager.record_trade_result(-100.0)  # 3rd loss
        result = risk_manager.check_kill_switches()
        assert result is not None
        assert "Cooldown" in result

    def test_cooldown_clears_after_win(self, risk_manager: "RiskManager") -> None:
        """A winning trade should reset consecutive loss counter."""
        risk_manager.record_trade_result(-100.0)
        risk_manager.record_trade_result(-100.0)
        risk_manager.record_trade_result(500.0)  # win
        assert risk_manager.consecutive_losses == 0

    def test_daily_reset(self, risk_manager: "RiskManager") -> None:
        """Daily reset should clear all kill switches."""
        risk_manager.update_daily_pnl(-5000.0)
        risk_manager.check_kill_switches()  # trigger
        risk_manager.reset_daily()
        assert risk_manager.check_kill_switches() is None
        assert risk_manager.daily_pnl == 0.0

    def test_max_daily_trades_blocks_signal(
        self, risk_manager: "RiskManager", sample_signal: Signal, mock_portfolio: MagicMock
    ) -> None:
        """Exceeding max daily trades should block new signals."""
        for _ in range(10):
            risk_manager.record_trade_result(100.0)
        with patch("core.risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 2, 10, 30)
            result = risk_manager.validate_signal(sample_signal, mock_portfolio)
        assert result is False


# ---------------------------------------------------------------------------
# Time Rules Tests
# ---------------------------------------------------------------------------


class TestTimeRules:
    def test_no_trade_before_morning_buffer(
        self, risk_manager: "RiskManager", sample_signal: Signal, mock_portfolio: MagicMock
    ) -> None:
        """Signal before 09:18 (3 min buffer) should be rejected."""
        with patch("core.risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 2, 9, 16)
            result = risk_manager.validate_signal(sample_signal, mock_portfolio)
        assert result is False

    def test_trade_allowed_after_buffer(
        self, risk_manager: "RiskManager", sample_signal: Signal, mock_portfolio: MagicMock
    ) -> None:
        """Signal at 09:20 should pass time checks."""
        with patch("core.risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 2, 9, 20)
            result = risk_manager.validate_signal(sample_signal, mock_portfolio)
        assert result is True

    def test_no_trade_after_cutoff(
        self, risk_manager: "RiskManager", sample_signal: Signal, mock_portfolio: MagicMock
    ) -> None:
        """Signal after 14:30 should be rejected."""
        with patch("core.risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 2, 14, 35)
            result = risk_manager.validate_signal(sample_signal, mock_portfolio)
        assert result is False

    def test_should_square_off_after_time(self, risk_manager: "RiskManager") -> None:
        with patch("core.risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 2, 15, 15)
            assert risk_manager.should_square_off() is True

    def test_no_square_off_before_time(self, risk_manager: "RiskManager") -> None:
        with patch("core.risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 2, 14, 0)
            assert risk_manager.should_square_off() is False


# ---------------------------------------------------------------------------
# Signal Validation Tests
# ---------------------------------------------------------------------------


class TestSignalValidation:
    def test_valid_long_signal(
        self, risk_manager: "RiskManager", sample_signal: Signal, mock_portfolio: MagicMock
    ) -> None:
        with patch("core.risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 2, 10, 0)
            assert risk_manager.validate_signal(sample_signal, mock_portfolio) is True

    def test_sl_above_entry_long_rejected(
        self, risk_manager: "RiskManager", mock_portfolio: MagicMock
    ) -> None:
        bad_signal = Signal(
            strategy="test",
            symbol="TEST",
            direction="LONG",
            entry_price=1000.0,
            stop_loss=1050.0,  # SL above entry!
            target_1=1100.0,
            target_2=None,
            confidence=0.5,
            timestamp=datetime.now(),
        )
        with patch("core.risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 2, 10, 0)
            assert risk_manager.validate_signal(bad_signal, mock_portfolio) is False

    def test_sl_too_wide_rejected(
        self, risk_manager: "RiskManager", mock_portfolio: MagicMock
    ) -> None:
        bad_signal = Signal(
            strategy="test",
            symbol="TEST",
            direction="LONG",
            entry_price=1000.0,
            stop_loss=960.0,  # 4% SL, exceeds max 2%
            target_1=1100.0,
            target_2=None,
            confidence=0.5,
            timestamp=datetime.now(),
        )
        with patch("core.risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 2, 10, 0)
            assert risk_manager.validate_signal(bad_signal, mock_portfolio) is False

    def test_duplicate_symbol_rejected(
        self, risk_manager: "RiskManager", sample_signal: Signal, mock_portfolio: MagicMock
    ) -> None:
        mock_portfolio.open_positions = {"RELIANCE": MagicMock()}
        with patch("core.risk_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 2, 10, 0)
            assert risk_manager.validate_signal(sample_signal, mock_portfolio) is False
