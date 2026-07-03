"""Unit tests for Mt5AccountClient — mocks the mt5 module (no live terminal)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from xauusd_bot.config.settings import AccountCredentials
from xauusd_bot.domain.enums import OrderSide, Timeframe, TradeStatus
from xauusd_bot.domain.models import TradePlan
from xauusd_bot.mt5_adapter.client import Mt5AccountClient
from xauusd_bot.mt5_adapter.errors import Mt5ConnectionError, Mt5OrderError


@pytest.fixture(autouse=True)
def reset_active_connection():
    """Reset the class-level active connection tracking to ensure test isolation."""
    Mt5AccountClient._active_account_id = None
    yield
    Mt5AccountClient._active_account_id = None


def _credentials(**overrides: object) -> AccountCredentials:
    base: dict[str, object] = dict(
        account_id=1,
        side="BUY",
        login=555111,
        password="pw",
        server="Broker-Demo",
        terminal_path="C:/MT5/t1.exe",
    )
    base.update(overrides)
    return AccountCredentials(**base)  # type: ignore[arg-type]


def _client(**kwargs: object) -> Mt5AccountClient:
    return Mt5AccountClient(_credentials(**kwargs), deviation_points=20, magic_number=234000)


def _plan(side: str = "BUY") -> TradePlan:
    return TradePlan(
        account_id=1,
        side=side,  # type: ignore[arg-type]
        symbol="XAUUSD",
        lot_size=0.05,
        stop_loss=1900.0,
        take_profit=2000.0,
        estimated_entry_price=1950.0,
    )


@pytest.fixture
def mt5_mock():
    with patch("xauusd_bot.mt5_adapter.client.mt5") as m:
        m.TIMEFRAME_M5 = 5
        m.TIMEFRAME_M15 = 15
        m.ORDER_TYPE_BUY = 0
        m.ORDER_TYPE_SELL = 1
        m.ORDER_TIME_GTC = 0
        m.TRADE_ACTION_DEAL = 1
        m.TRADE_RETCODE_DONE = 10009
        m.ORDER_FILLING_FOK = 0
        m.ORDER_FILLING_IOC = 1
        m.DEAL_ENTRY_OUT = 1
        yield m


def _symbol_info(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = dict(
        visible=True,
        digits=2,
        point=0.01,
        trade_tick_size=0.01,
        trade_tick_value=1.0,
        trade_contract_size=100.0,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
        filling_mode=2,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestConnect:
    def test_connect_success_passes_credentials_to_initialize(self, mt5_mock) -> None:
        mt5_mock.initialize.return_value = True
        mt5_mock.account_info.return_value = SimpleNamespace(login=555111)
        _client().connect()
        mt5_mock.initialize.assert_called_once_with(
            path="C:/MT5/t1.exe",
            login=555111,
            password="pw",
            server="Broker-Demo",
            timeout=10_000,
        )

    def test_initialize_failure_raises(self, mt5_mock) -> None:
        mt5_mock.initialize.return_value = False
        mt5_mock.last_error.return_value = (1, "boom")
        with pytest.raises(Mt5ConnectionError):
            _client().connect()

    def test_account_info_none_raises_and_shuts_down(self, mt5_mock) -> None:
        mt5_mock.initialize.return_value = True
        mt5_mock.account_info.return_value = None
        with pytest.raises(Mt5ConnectionError):
            _client().connect()
        mt5_mock.shutdown.assert_called_once()

    def test_account_mismatch_raises_and_shuts_down(self, mt5_mock) -> None:
        mt5_mock.initialize.return_value = True
        mt5_mock.account_info.return_value = SimpleNamespace(login=999999)
        with pytest.raises(Mt5ConnectionError):
            _client().connect()
        mt5_mock.shutdown.assert_called_once()


class TestGetSymbolSpec:
    def test_visible_symbol_maps_fields(self, mt5_mock) -> None:
        mt5_mock.symbol_info.return_value = _symbol_info()
        spec = _client().get_symbol_spec("XAUUSD")
        assert spec.tick_size == 0.01
        assert spec.contract_size == 100.0
        assert spec.volume_step == 0.01

    def test_symbol_info_none_raises(self, mt5_mock) -> None:
        mt5_mock.symbol_info.return_value = None
        with pytest.raises(Mt5ConnectionError):
            _client().get_symbol_spec("XAUUSD")

    def test_not_visible_then_selected_succeeds(self, mt5_mock) -> None:
        mt5_mock.symbol_info.side_effect = [
            _symbol_info(visible=False),
            _symbol_info(visible=True),
        ]
        mt5_mock.symbol_select.return_value = True
        spec = _client().get_symbol_spec("XAUUSD")
        mt5_mock.symbol_select.assert_called_once_with("XAUUSD", True)
        assert spec.symbol == "XAUUSD"

    def test_symbol_select_failure_raises(self, mt5_mock) -> None:
        mt5_mock.symbol_info.return_value = _symbol_info(visible=False)
        mt5_mock.symbol_select.return_value = False
        with pytest.raises(Mt5ConnectionError):
            _client().get_symbol_spec("XAUUSD")

    def test_still_not_visible_after_select_raises(self, mt5_mock) -> None:
        mt5_mock.symbol_info.side_effect = [
            _symbol_info(visible=False),
            _symbol_info(visible=False),
        ]
        mt5_mock.symbol_select.return_value = True
        with pytest.raises(Mt5ConnectionError):
            _client().get_symbol_spec("XAUUSD")


class TestGetCandles:
    _DTYPE = [
        ("time", "i8"),
        ("open", "f8"),
        ("high", "f8"),
        ("low", "f8"),
        ("close", "f8"),
        ("tick_volume", "i8"),
        ("spread", "i4"),
        ("real_volume", "i8"),
    ]

    def test_maps_rates_using_tick_volume(self, mt5_mock) -> None:
        arr = np.array(
            [(1_700_000_000, 1950.0, 1955.0, 1945.0, 1952.0, 123, 10, 0)],
            dtype=self._DTYPE,
        )
        mt5_mock.copy_rates_from_pos.return_value = arr
        candles = _client().get_candles("XAUUSD", Timeframe.M5, 1)
        assert len(candles) == 1
        assert candles[0].volume == 123
        assert candles[0].close == 1952.0
        mt5_mock.copy_rates_from_pos.assert_called_once_with("XAUUSD", mt5_mock.TIMEFRAME_M5, 0, 1)

    def test_none_response_raises(self, mt5_mock) -> None:
        mt5_mock.copy_rates_from_pos.return_value = None
        with pytest.raises(Mt5ConnectionError):
            _client().get_candles("XAUUSD", Timeframe.M5, 10)

    def test_empty_response_raises(self, mt5_mock) -> None:
        mt5_mock.copy_rates_from_pos.return_value = np.array([], dtype=self._DTYPE)
        with pytest.raises(Mt5ConnectionError):
            _client().get_candles("XAUUSD", Timeframe.M5, 10)


class TestGetCurrentTick:
    def test_success(self, mt5_mock) -> None:
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(
            bid=1950.10, ask=1950.30, time=1_700_000_000
        )
        tick = _client().get_current_tick("XAUUSD")
        assert tick.bid == 1950.10
        assert tick.ask == 1950.30

    def test_none_raises(self, mt5_mock) -> None:
        mt5_mock.symbol_info_tick.return_value = None
        with pytest.raises(Mt5ConnectionError):
            _client().get_current_tick("XAUUSD")

    def test_non_positive_bid_raises(self, mt5_mock) -> None:
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(
            bid=0.0, ask=1950.3, time=1_700_000_000
        )
        with pytest.raises(Mt5ConnectionError):
            _client().get_current_tick("XAUUSD")


class TestSendMarketOrder:
    def test_buy_uses_ask_and_ioc_filling(self, mt5_mock) -> None:
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(
            bid=1950.0, ask=1950.3, time=1_700_000_000
        )
        mt5_mock.symbol_info.return_value = _symbol_info(filling_mode=2)  # IOC bit only
        mt5_mock.order_send.return_value = SimpleNamespace(
            retcode=mt5_mock.TRADE_RETCODE_DONE, order=123456, price=1950.3, comment="done"
        )
        client = Mt5AccountClient(_credentials(), deviation_points=25, magic_number=777)
        result = client.send_market_order(_plan("BUY"))

        assert result.success is True
        assert result.order_ticket == 123456
        sent = mt5_mock.order_send.call_args[0][0]
        assert sent["type"] == mt5_mock.ORDER_TYPE_BUY
        assert sent["price"] == 1950.3
        assert sent["type_filling"] == mt5_mock.ORDER_FILLING_IOC
        assert sent["deviation"] == 25
        assert sent["magic"] == 777
        assert "position" not in sent

    def test_sell_uses_bid(self, mt5_mock) -> None:
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(
            bid=1950.0, ask=1950.3, time=1_700_000_000
        )
        mt5_mock.symbol_info.return_value = _symbol_info(filling_mode=2)
        mt5_mock.order_send.return_value = SimpleNamespace(
            retcode=mt5_mock.TRADE_RETCODE_DONE, order=1, price=1950.0, comment="done"
        )
        _client().send_market_order(_plan("SELL"))
        sent = mt5_mock.order_send.call_args[0][0]
        assert sent["type"] == mt5_mock.ORDER_TYPE_SELL
        assert sent["price"] == 1950.0

    def test_prefers_ioc_over_fok_when_both_supported(self, mt5_mock) -> None:
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(
            bid=1950.0, ask=1950.3, time=1_700_000_000
        )
        mt5_mock.symbol_info.return_value = _symbol_info(filling_mode=3)  # both bits set
        mt5_mock.order_send.return_value = SimpleNamespace(
            retcode=mt5_mock.TRADE_RETCODE_DONE, order=1, price=1950.3, comment="done"
        )
        _client().send_market_order(_plan("BUY"))
        sent = mt5_mock.order_send.call_args[0][0]
        assert sent["type_filling"] == mt5_mock.ORDER_FILLING_IOC

    def test_falls_back_to_fok(self, mt5_mock) -> None:
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(
            bid=1950.0, ask=1950.3, time=1_700_000_000
        )
        mt5_mock.symbol_info.return_value = _symbol_info(filling_mode=1)  # FOK bit only
        mt5_mock.order_send.return_value = SimpleNamespace(
            retcode=mt5_mock.TRADE_RETCODE_DONE, order=1, price=1950.3, comment="done"
        )
        _client().send_market_order(_plan("BUY"))
        sent = mt5_mock.order_send.call_args[0][0]
        assert sent["type_filling"] == mt5_mock.ORDER_FILLING_FOK

    def test_no_supported_filling_mode_raises(self, mt5_mock) -> None:
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(
            bid=1950.0, ask=1950.3, time=1_700_000_000
        )
        mt5_mock.symbol_info.return_value = _symbol_info(filling_mode=0)
        with pytest.raises(Mt5OrderError):
            _client().send_market_order(_plan())

    def test_rejected_retcode_raises(self, mt5_mock) -> None:
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(
            bid=1950.0, ask=1950.3, time=1_700_000_000
        )
        mt5_mock.symbol_info.return_value = _symbol_info(filling_mode=2)
        mt5_mock.order_send.return_value = SimpleNamespace(
            retcode=99999, order=None, price=None, comment="rejected"
        )
        with pytest.raises(Mt5OrderError):
            _client().send_market_order(_plan())

    def test_order_send_none_raises(self, mt5_mock) -> None:
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(
            bid=1950.0, ask=1950.3, time=1_700_000_000
        )
        mt5_mock.symbol_info.return_value = _symbol_info(filling_mode=2)
        mt5_mock.order_send.return_value = None
        mt5_mock.last_error.return_value = (1, "no connection")
        with pytest.raises(Mt5OrderError):
            _client().send_market_order(_plan())


class TestClosePosition:
    def test_closes_with_opposite_side_and_position_ticket(self, mt5_mock) -> None:
        mt5_mock.symbol_info_tick.return_value = SimpleNamespace(
            bid=1949.0, ask=1949.2, time=1_700_000_000
        )
        mt5_mock.symbol_info.return_value = _symbol_info(filling_mode=2)
        mt5_mock.order_send.return_value = SimpleNamespace(
            retcode=mt5_mock.TRADE_RETCODE_DONE, order=999, price=1949.0, comment="closed"
        )
        result = _client().close_position(
            account_id=1,
            symbol="XAUUSD",
            side=OrderSide.BUY,
            volume=0.05,
            position_ticket=123456,
        )
        sent = mt5_mock.order_send.call_args[0][0]
        assert sent["type"] == mt5_mock.ORDER_TYPE_SELL
        assert sent["position"] == 123456
        assert sent["sl"] == 0.0 and sent["tp"] == 0.0
        assert result.success is True


def _deal(**overrides: object) -> SimpleNamespace:
    base: dict[str, object] = dict(
        entry=1,  # DEAL_ENTRY_OUT
        price=2000.0,
        profit=150.0,
        time=1_700_000_100,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestGetPositionStatus:
    def test_still_open(self, mt5_mock) -> None:
        mt5_mock.positions_get.return_value = (SimpleNamespace(ticket=123456),)
        result = _client().get_position_status(
            ticket=123456, symbol="XAUUSD", stop_loss=1900.0, take_profit=2000.0
        )
        assert result.still_open is True
        assert result.close_status is None

    def test_positions_get_none_raises(self, mt5_mock) -> None:
        mt5_mock.positions_get.return_value = None
        mt5_mock.last_error.return_value = (1, "no connection")
        with pytest.raises(Mt5ConnectionError):
            _client().get_position_status(
                ticket=123456, symbol="XAUUSD", stop_loss=1900.0, take_profit=2000.0
            )

    def test_closed_at_take_profit(self, mt5_mock) -> None:
        mt5_mock.positions_get.return_value = ()
        mt5_mock.history_deals_get.return_value = (_deal(price=2000.0, profit=150.0),)
        mt5_mock.symbol_info.return_value = _symbol_info(point=0.01)
        result = _client().get_position_status(
            ticket=123456, symbol="XAUUSD", stop_loss=1900.0, take_profit=2000.0
        )
        assert result.still_open is False
        assert result.close_status == TradeStatus.CLOSED_TP
        assert result.close_price == 2000.0
        assert result.profit_usd == 150.0

    def test_closed_at_stop_loss(self, mt5_mock) -> None:
        mt5_mock.positions_get.return_value = ()
        mt5_mock.history_deals_get.return_value = (_deal(price=1900.0, profit=-50.0),)
        mt5_mock.symbol_info.return_value = _symbol_info(point=0.01)
        result = _client().get_position_status(
            ticket=123456, symbol="XAUUSD", stop_loss=1900.0, take_profit=2000.0
        )
        assert result.close_status == TradeStatus.CLOSED_SL
        assert result.profit_usd == -50.0

    def test_closed_manually_matches_neither(self, mt5_mock) -> None:
        mt5_mock.positions_get.return_value = ()
        mt5_mock.history_deals_get.return_value = (_deal(price=1950.0, profit=20.0),)
        mt5_mock.symbol_info.return_value = _symbol_info(point=0.01)
        result = _client().get_position_status(
            ticket=123456, symbol="XAUUSD", stop_loss=1900.0, take_profit=2000.0
        )
        assert result.close_status == TradeStatus.CLOSED_MANUAL

    def test_no_exit_deal_found_is_undetermined(self, mt5_mock) -> None:
        mt5_mock.positions_get.return_value = ()
        mt5_mock.history_deals_get.return_value = (_deal(entry=0),)  # DEAL_ENTRY_IN only
        result = _client().get_position_status(
            ticket=123456, symbol="XAUUSD", stop_loss=1900.0, take_profit=2000.0
        )
        assert result.still_open is False
        assert result.close_status is None

    def test_history_deals_get_none_raises(self, mt5_mock) -> None:
        mt5_mock.positions_get.return_value = ()
        mt5_mock.history_deals_get.return_value = None
        mt5_mock.last_error.return_value = (1, "no connection")
        with pytest.raises(Mt5ConnectionError):
            _client().get_position_status(
                ticket=123456, symbol="XAUUSD", stop_loss=1900.0, take_profit=2000.0
            )
