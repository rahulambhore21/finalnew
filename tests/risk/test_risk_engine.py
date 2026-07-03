"""Unit tests for RiskEngine — pure calculator, no I/O, no mocking needed."""

from __future__ import annotations

import pytest

from xauusd_bot.config.settings import AccountCredentials
from xauusd_bot.domain.models import RiskParameters, SymbolSpec
from xauusd_bot.risk.errors import RiskCalculationError
from xauusd_bot.risk.risk_engine import RiskEngine


def _account(account_id: int, side: str) -> AccountCredentials:
    return AccountCredentials(
        account_id=account_id,
        side=side,
        login=100000 + account_id,
        password="dummy-password",
        server="TestBroker-Server",
        terminal_path=f"C:/MT5/terminal_{account_id}.exe",
    )


def _spec(
    symbol: str = "XAUUSD",
    digits: int = 2,
    point: float = 0.01,
    tick_size: float = 0.01,
    tick_value: float = 1.0,
    contract_size: float = 100.0,
    volume_min: float = 0.01,
    volume_max: float = 50.0,
    volume_step: float = 0.01,
) -> SymbolSpec:
    return SymbolSpec(
        symbol=symbol,
        digits=digits,
        point=point,
        tick_size=tick_size,
        tick_value=tick_value,
        contract_size=contract_size,
        volume_min=volume_min,
        volume_max=volume_max,
        volume_step=volume_step,
    )


def _risk_params(risk_usd: float = 50.0, reward_usd: float = 150.0) -> RiskParameters:
    return RiskParameters(
        risk_usd=risk_usd,
        reward_usd=reward_usd,
        lot_min=0.05,
        lot_max=0.10,
        preferred_lot_size=0.05,
    )


_ACCOUNTS = (
    _account(1, "BUY"),
    _account(2, "BUY"),
    _account(3, "SELL"),
    _account(4, "SELL"),
)


def test_identical_specs_buy_sell_produces_expected_lot_and_prices() -> None:
    spec = _spec()
    symbol_specs = {1: spec, 2: spec, 3: spec, 4: spec}
    engine = RiskEngine(_risk_params())

    plans = engine.compute_trade_plans(_ACCOUNTS, symbol_specs, entry_price=1950.00)
    by_id = {p.account_id: p for p in plans}

    assert len(plans) == 4
    for p in plans:
        assert p.lot_size == 0.05
        assert p.symbol == "XAUUSD"
        assert p.estimated_entry_price == 1950.00

    assert by_id[1].side == "BUY"
    assert by_id[1].stop_loss == 1940.00
    assert by_id[1].take_profit == 1980.00

    assert by_id[2].side == "BUY"
    assert by_id[2].stop_loss == 1940.00
    assert by_id[2].take_profit == 1980.00

    assert by_id[3].side == "SELL"
    assert by_id[3].stop_loss == 1960.00
    assert by_id[3].take_profit == 1920.00

    assert by_id[4].side == "SELL"
    assert by_id[4].stop_loss == 1960.00
    assert by_id[4].take_profit == 1920.00


def test_differing_tick_value_per_broker_keeps_shared_lot_correct_per_account_prices() -> None:
    symbol_specs = {
        1: _spec(tick_value=1.0),
        2: _spec(tick_value=0.98),
        3: _spec(tick_value=1.02),
        4: _spec(tick_value=1.0),
    }
    engine = RiskEngine(_risk_params())

    plans = engine.compute_trade_plans(_ACCOUNTS, symbol_specs, entry_price=1950.00)
    by_id = {p.account_id: p for p in plans}

    for p in plans:
        assert p.lot_size == 0.05  # shared across accounts regardless of tick_value

    assert by_id[1].stop_loss == 1940.00
    assert by_id[1].take_profit == 1980.00

    assert by_id[2].stop_loss == 1939.80
    assert by_id[2].take_profit == 1980.62

    assert by_id[3].stop_loss == 1959.80
    assert by_id[3].take_profit == 1920.58

    assert by_id[4].stop_loss == 1960.00
    assert by_id[4].take_profit == 1920.00


def test_no_valid_shared_lot_size_raises() -> None:
    symbol_specs = {
        1: _spec(),
        2: _spec(volume_min=0.15),  # exceeds configured lot_max=0.10
        3: _spec(),
        4: _spec(),
    }
    engine = RiskEngine(_risk_params())

    with pytest.raises(RiskCalculationError):
        engine.compute_trade_plans(_ACCOUNTS, symbol_specs, entry_price=1950.00)


def test_zero_distance_stop_loss_raises() -> None:
    spec = _spec()
    symbol_specs = {1: spec, 2: spec, 3: spec, 4: spec}
    # risk_usd tiny relative to lot_size/tick_value -> SL distance floors to 0
    engine = RiskEngine(_risk_params(risk_usd=0.03))

    with pytest.raises(RiskCalculationError):
        engine.compute_trade_plans(_ACCOUNTS, symbol_specs, entry_price=1950.00)


def test_inconsistent_tick_value_raises() -> None:
    # tick_size reported as 0.1 but tick_value left at the 0.01-tick value (1.0)
    # instead of scaling to 10.0 -- contract_size*tick_size = 10.0, an 90%
    # deviation from the reported tick_value=1.0. Reproduces a real incident
    # where this silently produced a 10x-oversized SL/TP distance.
    spec = _spec(tick_size=0.1, tick_value=1.0, contract_size=100.0)
    symbol_specs = {1: spec, 2: spec, 3: spec, 4: spec}
    engine = RiskEngine(_risk_params())

    with pytest.raises(RiskCalculationError, match="tick_value sanity check failed"):
        engine.compute_trade_plans(_ACCOUNTS, symbol_specs, entry_price=1950.00)


def test_buy_sell_direction_correctness() -> None:
    spec = _spec()
    symbol_specs = {1: spec, 2: spec, 3: spec, 4: spec}
    engine = RiskEngine(_risk_params())
    entry_price = 1950.00

    plans = engine.compute_trade_plans(_ACCOUNTS, symbol_specs, entry_price)

    for p in plans:
        if p.side == "BUY":
            assert p.stop_loss < entry_price < p.take_profit
        else:
            assert p.take_profit < entry_price < p.stop_loss


def test_preferred_lot_size_override() -> None:
    spec = _spec()
    symbol_specs = {1: spec, 2: spec, 3: spec, 4: spec}
    engine = RiskEngine(_risk_params(risk_usd=50.0, reward_usd=150.0))

    plans = engine.compute_trade_plans(_ACCOUNTS, symbol_specs, entry_price=1950.00, preferred_lot_size=0.08)
    for p in plans:
        assert p.lot_size == 0.08


def test_too_wide_stop_loss_raises_when_ai_lot_below_lot_min() -> None:
    """When the AI recommends a lot size below the configured lot_min (implying the
    stop loss is too wide for safe sizing), compute_trade_plans must raise
    RiskCalculationError so the trading engine skips the trade."""
    spec = _spec()
    symbol_specs = {1: spec, 2: spec, 3: spec, 4: spec}
    engine = RiskEngine(_risk_params())  # lot_min=0.05

    with pytest.raises(RiskCalculationError, match="stop loss is too wide"):
        engine.compute_trade_plans(
            _ACCOUNTS, symbol_specs, entry_price=1950.00, preferred_lot_size=0.03
        )


def test_ai_lot_exactly_at_lot_min_is_accepted() -> None:
    """A lot size exactly at the configured lot_min boundary must be accepted."""
    spec = _spec()
    symbol_specs = {1: spec, 2: spec, 3: spec, 4: spec}
    engine = RiskEngine(_risk_params())  # lot_min=0.05

    plans = engine.compute_trade_plans(
        _ACCOUNTS, symbol_specs, entry_price=1950.00, preferred_lot_size=0.05
    )
    assert len(plans) == 4
    for p in plans:
        assert p.lot_size == 0.05
