"""Unit tests for MultiAccountExecutor — uses fake Mt5AccountClient collaborators."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from xauusd_bot.domain.models import (
    ExecutionResult,
    OpenTradeQuery,
    PositionCheckResult,
    SymbolSpec,
    TradePlan,
)
from xauusd_bot.mt5_adapter.errors import Mt5ConnectionError, Mt5OrderError
from xauusd_bot.mt5_adapter.executor import MultiAccountExecutor


def _plan(account_id: int, side: str = "BUY") -> TradePlan:
    return TradePlan(
        account_id=account_id,
        side=side,  # type: ignore[arg-type]
        symbol="XAUUSD",
        lot_size=0.05,
        stop_loss=1900.0,
        take_profit=2000.0,
        estimated_entry_price=1950.0,
    )


def _fake_client(account_id: int) -> MagicMock:
    client = MagicMock()
    client.account_id = account_id
    return client


def _spec(account_id: int) -> SymbolSpec:
    return SymbolSpec(
        symbol="XAUUSD",
        digits=2,
        point=0.01,
        tick_size=0.01,
        tick_value=1.0,
        contract_size=100.0,
        volume_min=0.01,
        volume_max=50.0,
        volume_step=0.01,
    )


def _open_trade_query(trade_id: int, account_id: int) -> OpenTradeQuery:
    return OpenTradeQuery(
        trade_id=trade_id,
        account_id=account_id,
        symbol="XAUUSD",
        mt5_order_ticket=1000 + trade_id,
        stop_loss=1900.0,
        take_profit=2000.0,
    )


class TestAllSucceed:
    def test_no_rollback_when_all_succeed(self) -> None:
        clients = [_fake_client(i) for i in (1, 2, 3, 4)]
        for i, client in zip((1, 2, 3, 4), clients):
            client.send_market_order.return_value = ExecutionResult(
                account_id=i, success=True, order_ticket=100 + i, entry_price=1950.0
            )
        executor = MultiAccountExecutor(clients)
        results = executor.execute_all([_plan(i) for i in (1, 2, 3, 4)])

        assert all(r.success for r in results)
        assert all(not r.rolled_back for r in results)
        for client in clients:
            client.connect.assert_called_once()
            client.disconnect.assert_called_once()
            client.close_position.assert_not_called()


class TestPartialFailureTriggersRollback:
    def test_succeeded_accounts_are_rolled_back(self) -> None:
        clients = {i: _fake_client(i) for i in (1, 2, 3, 4)}
        clients[1].send_market_order.return_value = ExecutionResult(
            account_id=1, success=True, order_ticket=101, entry_price=1950.0
        )
        clients[2].send_market_order.return_value = ExecutionResult(
            account_id=2, success=True, order_ticket=102, entry_price=1950.0
        )
        clients[3].send_market_order.side_effect = Mt5OrderError("rejected")
        clients[4].send_market_order.return_value = ExecutionResult(
            account_id=4, success=True, order_ticket=104, entry_price=1950.0
        )

        executor = MultiAccountExecutor(list(clients.values()))
        results = executor.execute_all([_plan(i) for i in (1, 2, 3, 4)])

        assert all(not r.success for r in results)
        clients[1].close_position.assert_called_once()
        clients[2].close_position.assert_called_once()
        clients[4].close_position.assert_called_once()
        clients[3].close_position.assert_not_called()

        by_id = {r.account_id: r for r in results}
        assert by_id[1].rolled_back is True
        assert by_id[3].rolled_back is False
        assert "rejected" in by_id[3].error_message

    def test_rollback_close_failure_is_reported_not_raised(self, caplog: pytest.LogCaptureFixture) -> None:
        clients = {i: _fake_client(i) for i in (1, 2)}
        clients[1].send_market_order.return_value = ExecutionResult(
            account_id=1, success=True, order_ticket=101, entry_price=1950.0
        )
        clients[2].send_market_order.side_effect = Mt5OrderError("rejected on 2")
        clients[1].close_position.side_effect = Mt5OrderError("cannot close - market closed")

        executor = MultiAccountExecutor(list(clients.values()))
        with caplog.at_level("CRITICAL"):
            results = executor.execute_all([_plan(1), _plan(2)])

        by_id = {r.account_id: r for r in results}
        assert by_id[1].success is False
        assert by_id[1].rolled_back is False
        assert "MANUAL INTERVENTION" in by_id[1].error_message
        assert any("MANUAL INTERVENTION" in rec.message for rec in caplog.records)

    def test_connect_failure_on_one_account_does_not_stop_others(self) -> None:
        clients = {i: _fake_client(i) for i in (1, 2)}
        clients[1].connect.side_effect = Mt5ConnectionError("terminal not running")
        clients[2].send_market_order.return_value = ExecutionResult(
            account_id=2, success=True, order_ticket=202, entry_price=1950.0
        )

        executor = MultiAccountExecutor(list(clients.values()))
        results = executor.execute_all([_plan(1), _plan(2)])

        by_id = {r.account_id: r for r in results}
        assert by_id[1].success is False
        assert by_id[2].rolled_back is True
        clients[2].close_position.assert_called_once()
        clients[1].send_market_order.assert_not_called()  # never reached after connect() failed


class TestValidation:
    def test_unknown_account_id_raises_value_error(self) -> None:
        executor = MultiAccountExecutor([_fake_client(1)])
        with pytest.raises(ValueError):
            executor.execute_all([_plan(99)])

    def test_duplicate_account_id_raises_value_error(self) -> None:
        executor = MultiAccountExecutor([_fake_client(1)])
        with pytest.raises(ValueError):
            executor.execute_all([_plan(1), _plan(1)])


class TestGetSymbolSpecs:
    def test_all_succeed(self) -> None:
        clients = {i: _fake_client(i) for i in (1, 2, 3, 4)}
        for i, client in clients.items():
            client.get_symbol_spec.return_value = _spec(i)

        executor = MultiAccountExecutor(list(clients.values()))
        specs = executor.get_symbol_specs("XAUUSD")

        assert set(specs.keys()) == {1, 2, 3, 4}
        for client in clients.values():
            client.connect.assert_called_once()
            client.disconnect.assert_called_once()

    def test_one_account_failure_propagates(self) -> None:
        clients = {i: _fake_client(i) for i in (1, 2)}
        clients[1].get_symbol_spec.return_value = _spec(1)
        clients[2].get_symbol_spec.side_effect = Mt5ConnectionError("no symbol")

        executor = MultiAccountExecutor(list(clients.values()))
        with pytest.raises(Mt5ConnectionError):
            executor.get_symbol_specs("XAUUSD")

        clients[2].disconnect.assert_called_once()


class TestCheckTradeStatuses:
    def test_mixed_still_open_and_closed(self) -> None:
        clients = {i: _fake_client(i) for i in (1, 2)}
        clients[1].get_position_status.return_value = PositionCheckResult(
            ticket=1001, still_open=True
        )
        clients[2].get_position_status.return_value = PositionCheckResult(
            ticket=1002, still_open=False, close_status=None, close_price=1999.0, profit_usd=100.0
        )

        executor = MultiAccountExecutor(list(clients.values()))
        results = executor.check_trade_statuses(
            [_open_trade_query(1, 1), _open_trade_query(2, 2)]
        )

        assert results[1].still_open is True
        assert results[2].still_open is False
        clients[1].disconnect.assert_called_once()
        clients[2].disconnect.assert_called_once()

    def test_connect_failure_on_one_account_does_not_block_others(self) -> None:
        clients = {i: _fake_client(i) for i in (1, 2)}
        clients[1].connect.side_effect = Mt5ConnectionError("terminal down")
        clients[2].get_position_status.return_value = PositionCheckResult(
            ticket=1002, still_open=True
        )

        executor = MultiAccountExecutor(list(clients.values()))
        results = executor.check_trade_statuses(
            [_open_trade_query(1, 1), _open_trade_query(2, 2)]
        )

        assert results[1].still_open is True  # unresolved, not raised
        assert results[2].still_open is True
        clients[1].get_position_status.assert_not_called()

    def test_lookup_failure_is_reported_as_unresolved(self) -> None:
        clients = {1: _fake_client(1)}
        clients[1].get_position_status.side_effect = Mt5ConnectionError("history unavailable")

        executor = MultiAccountExecutor(list(clients.values()))
        results = executor.check_trade_statuses([_open_trade_query(1, 1)])

        assert results[1].still_open is True
        clients[1].disconnect.assert_called_once()

    def test_unknown_account_id_is_reported_as_unresolved(self) -> None:
        executor = MultiAccountExecutor([_fake_client(1)])
        results = executor.check_trade_statuses([_open_trade_query(1, 99)])
        assert results[1].still_open is True
