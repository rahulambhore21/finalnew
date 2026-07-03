"""Unit tests for TradingEngine — fakes AIAnalyzer/MT5, uses a real in-memory DB."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from xauusd_bot.ai.errors import AIAnalysisError
from xauusd_bot.config.settings import AISettings, DatabaseSettings, LoggingSettings, Settings, TradingSettings
from xauusd_bot.database.connection import init_db
from xauusd_bot.database.repositories import AnalysisRepository, OpportunityRepository, TradeRepository
from xauusd_bot.domain.enums import TradeStatus, TriggerLevel
from xauusd_bot.domain.models import (
    Candle,
    ExecutionResult,
    MarketAnalysis,
    PositionCheckResult,
    RiskParameters,
    SymbolSpec,
    Tick,
    TradePlan,
)
from xauusd_bot.engine.trading_engine import TradingEngine
from xauusd_bot.mt5_adapter.client import Mt5AccountClient
from xauusd_bot.mt5_adapter.executor import MultiAccountExecutor
from xauusd_bot.risk.risk_engine import RiskEngine


def _settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        mt5_account_1_side="BUY",
        mt5_account_1_login=1001,
        mt5_account_1_password="pw1",
        mt5_account_1_server="Broker-Demo",
        mt5_account_1_terminal_path="C:/MT5/t1.exe",
        mt5_account_2_side="BUY",
        mt5_account_2_login=1002,
        mt5_account_2_password="pw2",
        mt5_account_2_server="Broker-Demo",
        mt5_account_2_terminal_path="C:/MT5/t2.exe",
        mt5_account_3_side="SELL",
        mt5_account_3_login=1003,
        mt5_account_3_password="pw3",
        mt5_account_3_server="Broker-Demo",
        mt5_account_3_terminal_path="C:/MT5/t3.exe",
        mt5_account_4_side="SELL",
        mt5_account_4_login=1004,
        mt5_account_4_password="pw4",
        mt5_account_4_server="Broker-Demo",
        mt5_account_4_terminal_path="C:/MT5/t4.exe",
        trading=TradingSettings(
            symbol="XAUUSD",
            timeframes=["M5", "M15"],
            risk_usd=50.0,
            reward_usd=150.0,
            lot_min=0.05,
            lot_max=0.10,
            preferred_lot_size=0.05,
            poll_interval_seconds=1.0,
        ),
        ai=AISettings(
            model="gpt-4o-mini",
            temperature=0.2,
            max_retries=3,
            timeout_seconds=30,
            m5_candle_count=3,
            m15_candle_count=3,
        ),
        database=DatabaseSettings(path=Path("data/test.db")),
        logging=LoggingSettings(level="INFO", dir=Path("logs"), max_bytes=10_485_760, backup_count=5),
    )


def _spec() -> SymbolSpec:
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


def _m5_candles(last_time: datetime, count: int = 3) -> list[Candle]:
    return [
        Candle(
            time=last_time - timedelta(minutes=5 * (count - 1 - i)),
            open=1950.0,
            high=1951.0,
            low=1949.0,
            close=1950.0,
            volume=100,
        )
        for i in range(count)
    ]


def _tick(bid: float, ask: float, time: datetime) -> Tick:
    return Tick(bid=bid, ask=ask, time=time)


def _analysis(support: float, resistance: float, candle_time: datetime) -> MarketAnalysis:
    return MarketAnalysis(
        support=support,
        resistance=resistance,
        confidence=0.8,
        reason="test analysis",
        analyzed_at=candle_time,
        candle_time=candle_time,
        symbol="XAUUSD",
        model="gpt-4o-mini",
    )


class FakeAIAnalyzer:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple] = []

    def analyze(self, symbol, m5_candles, m15_candles):
        self.calls.append((symbol, m5_candles, m15_candles))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    yield conn
    conn.close()


@pytest.fixture
def repos(db_conn):
    return SimpleNamespace(
        opportunity=OpportunityRepository(db_conn),
        analysis=AnalysisRepository(db_conn),
        trade=TradeRepository(db_conn),
    )


def _make_engine(repos, ai_analyzer, executor=None, market_data_client=None):
    settings = _settings()
    risk_engine = RiskEngine(
        RiskParameters(
            risk_usd=settings.trading.risk_usd,
            reward_usd=settings.trading.reward_usd,
            lot_min=settings.trading.lot_min,
            lot_max=settings.trading.lot_max,
            preferred_lot_size=settings.trading.preferred_lot_size,
        )
    )
    executor = executor if executor is not None else MagicMock(spec=MultiAccountExecutor)
    market_data_client = (
        market_data_client if market_data_client is not None else MagicMock(spec=Mt5AccountClient)
    )
    engine = TradingEngine(
        settings=settings,
        ai_analyzer=ai_analyzer,
        risk_engine=risk_engine,
        executor=executor,
        market_data_client=market_data_client,
        opportunity_repo=repos.opportunity,
        analysis_repo=repos.analysis,
        trade_repo=repos.trade,
    )
    return engine, executor, market_data_client, settings


class TestNoNewCandle:
    def test_no_ai_call_when_candle_time_unchanged(self, repos, db_conn) -> None:
        candle_time = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        repos.analysis.insert_analysis(_analysis(1900.0, 2000.0, candle_time))

        ai_analyzer = FakeAIAnalyzer([])
        client = MagicMock(spec=Mt5AccountClient)
        client.get_current_tick.return_value = _tick(1950.0, 1950.2, candle_time)
        client.get_candles.return_value = _m5_candles(candle_time)

        engine, executor, _, _ = _make_engine(repos, ai_analyzer, market_data_client=client)
        engine.run_once()

        assert ai_analyzer.calls == []
        executor.execute_all.assert_not_called()


class TestNewCandleTriggersAnalysis:
    def test_ai_failure_is_caught_and_retried_next_cycle(self, repos, db_conn) -> None:
        candle_time = datetime(2026, 7, 1, 10, 5, tzinfo=timezone.utc)
        ai_analyzer = FakeAIAnalyzer([AIAnalysisError("OpenAI request failed")])
        client = MagicMock(spec=Mt5AccountClient)
        client.get_current_tick.return_value = _tick(1950.0, 1950.2, candle_time)
        client.get_candles.return_value = _m5_candles(candle_time)

        engine, executor, _, _ = _make_engine(repos, ai_analyzer, market_data_client=client)
        engine.run_once()  # must not raise

        assert len(ai_analyzer.calls) == 1
        assert repos.analysis.get_latest_analysis() is None
        executor.execute_all.assert_not_called()

    def test_ai_called_once_and_analysis_persisted(self, repos, db_conn) -> None:
        candle_time = datetime(2026, 7, 1, 10, 5, tzinfo=timezone.utc)
        analysis = _analysis(1900.0, 2000.0, candle_time)

        ai_analyzer = FakeAIAnalyzer([analysis])
        client = MagicMock(spec=Mt5AccountClient)
        client.get_current_tick.return_value = _tick(1950.0, 1950.2, candle_time)
        client.get_candles.return_value = _m5_candles(candle_time)

        engine, executor, _, _ = _make_engine(repos, ai_analyzer, market_data_client=client)
        engine.run_once()

        assert len(ai_analyzer.calls) == 1
        persisted = repos.analysis.get_latest_analysis()
        assert persisted is not None
        assert persisted.support == 1900.0
        assert persisted.resistance == 2000.0
        executor.execute_all.assert_not_called()
        assert repos.opportunity.has_opportunity_today(candle_time.date()) is False


class TestPriceAtSupportExecutes:
    def test_executes_with_correct_sides_and_persists(self, repos, db_conn) -> None:
        candle_time = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        repos.analysis.insert_analysis(_analysis(1900.0, 2000.0, candle_time))

        ai_analyzer = FakeAIAnalyzer([])
        client = MagicMock(spec=Mt5AccountClient)
        # bid touches support -> SUPPORT trigger at bid=1899.5
        client.get_current_tick.return_value = _tick(1899.5, 1899.7, candle_time)
        client.get_candles.return_value = _m5_candles(candle_time)

        executor = MagicMock(spec=MultiAccountExecutor)
        executor.get_symbol_specs.return_value = {i: _spec() for i in (1, 2, 3, 4)}
        executor.execute_all.return_value = [
            ExecutionResult(account_id=i, success=True, order_ticket=1000 + i, entry_price=1899.5)
            for i in (1, 2, 3, 4)
        ]

        engine, executor, _, settings = _make_engine(
            repos, ai_analyzer, executor=executor, market_data_client=client
        )
        engine.run_once()

        assert ai_analyzer.calls == []  # no new candle needed; cached analysis used
        assert repos.opportunity.has_opportunity_today(candle_time.date()) is True

        sent_plans = executor.execute_all.call_args[0][0]
        sides_by_account = {p.account_id: p.side for p in sent_plans}
        expected_sides = {a.account_id: a.side for a in settings.accounts}
        assert sides_by_account == expected_sides

        opp_row = db_conn.execute("SELECT id FROM daily_opportunities").fetchone()
        trades = repos.trade.get_trades_for_opportunity(opp_row["id"])
        assert len(trades) == 4
        assert all(t["status"] == "OPEN" for t in trades)
        assert all(t["mt5_order_ticket"] is not None for t in trades)


class TestAlreadyHasOpportunityToday:
    def test_skipped_no_ai_no_execution(self, repos, db_conn) -> None:
        candle_time = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        analysis_id = repos.analysis.insert_analysis(_analysis(1900.0, 2000.0, candle_time))
        repos.opportunity.record_opportunity(
            trading_date=candle_time.date(),
            triggered_at=candle_time,
            trigger_level=TriggerLevel.SUPPORT,
            trigger_price=1899.5,
            analysis_id=analysis_id,
        )

        ai_analyzer = FakeAIAnalyzer([])
        client = MagicMock(spec=Mt5AccountClient)
        client.get_current_tick.return_value = _tick(1899.5, 1899.7, candle_time)
        client.get_candles.return_value = _m5_candles(candle_time)

        engine, executor, _, _ = _make_engine(repos, ai_analyzer, market_data_client=client)
        engine.run_once()

        assert ai_analyzer.calls == []
        executor.execute_all.assert_not_called()


class TestOpenTradeMonitoring:
    def _seed_past_open_trade(self, repos, db_conn, past_date):
        past_candle_time = datetime.combine(past_date, datetime.min.time(), tzinfo=timezone.utc)
        analysis_id = repos.analysis.insert_analysis(
            _analysis(1900.0, 2000.0, past_candle_time)
        )
        opportunity_id = repos.opportunity.record_opportunity(
            trading_date=past_date,
            triggered_at=past_candle_time,
            trigger_level=TriggerLevel.SUPPORT,
            trigger_price=1899.5,
            analysis_id=analysis_id,
        )
        trade_plan = TradePlan(
            account_id=1, side="BUY", symbol="XAUUSD", lot_size=0.05,
            stop_loss=1900.0, take_profit=2000.0, estimated_entry_price=1899.5,
        )
        trade_id = repos.trade.insert_trade(opportunity_id, trade_plan)
        repos.trade.update_trade_status(
            trade_id, TradeStatus.OPEN, entry_price=1899.5, mt5_order_ticket=5001, opened_at=past_candle_time.isoformat()
        )
        return trade_id

    def test_closed_at_tp_updates_status_and_reopens_gate(self, repos, db_conn) -> None:
        past_date = datetime(2026, 6, 30, tzinfo=timezone.utc).date()
        trade_id = self._seed_past_open_trade(repos, db_conn, past_date)

        today = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        repos.analysis.insert_analysis(_analysis(1900.0, 2000.0, today))

        ai_analyzer = FakeAIAnalyzer([])
        client = MagicMock(spec=Mt5AccountClient)
        client.get_current_tick.return_value = _tick(1899.5, 1899.7, today)
        client.get_candles.return_value = _m5_candles(today)

        executor = MagicMock(spec=MultiAccountExecutor)
        executor.check_trade_statuses.return_value = {
            trade_id: PositionCheckResult(
                ticket=5001, still_open=False, close_status=TradeStatus.CLOSED_TP,
                close_price=2000.0, profit_usd=150.0, closed_at=today,
            )
        }
        executor.get_symbol_specs.return_value = {i: _spec() for i in (1, 2, 3, 4)}
        executor.execute_all.return_value = [
            ExecutionResult(account_id=i, success=True, order_ticket=2000 + i, entry_price=1899.5)
            for i in (1, 2, 3, 4)
        ]

        engine, executor, _, _ = _make_engine(
            repos, ai_analyzer, executor=executor, market_data_client=client
        )
        engine.run_once()

        trades = repos.trade.get_trades_for_opportunity(
            db_conn.execute(
                "SELECT opportunity_id FROM trades WHERE id = ?", (trade_id,)
            ).fetchone()["opportunity_id"]
        )
        closed_trade = next(t for t in trades if t["id"] == trade_id)
        assert closed_trade["status"] == "CLOSED_TP"
        assert closed_trade["close_price"] == 2000.0
        assert closed_trade["profit_usd"] == 150.0

        # gate reopened -> today's opportunity was recorded
        assert repos.opportunity.has_opportunity_today(today.date()) is True

    def test_unresolved_open_trade_blocks_new_analysis(self, repos, db_conn) -> None:
        past_date = datetime(2026, 6, 30, tzinfo=timezone.utc).date()
        trade_id = self._seed_past_open_trade(repos, db_conn, past_date)

        today = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)

        ai_analyzer = FakeAIAnalyzer([_analysis(1900.0, 2000.0, today)])
        client = MagicMock(spec=Mt5AccountClient)
        client.get_current_tick.return_value = _tick(1899.5, 1899.7, today)
        client.get_candles.return_value = _m5_candles(today)

        executor = MagicMock(spec=MultiAccountExecutor)
        executor.check_trade_statuses.return_value = {
            trade_id: PositionCheckResult(ticket=5001, still_open=True)
        }

        engine, executor, _, _ = _make_engine(
            repos, ai_analyzer, executor=executor, market_data_client=client
        )
        engine.run_once()

        assert ai_analyzer.calls == []
        assert repos.opportunity.has_opportunity_today(today.date()) is False
        row = db_conn.execute("SELECT status FROM trades WHERE id = ?", (trade_id,)).fetchone()
        assert row["status"] == "OPEN"


class TestMaxOpportunitiesPerDay:
    def test_allows_multiple_opportunities_when_configured(self, repos, db_conn) -> None:
        candle_time = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        analysis_id = repos.analysis.insert_analysis(_analysis(1900.0, 2000.0, candle_time))

        # Record one opportunity today
        repos.opportunity.record_opportunity(
            trading_date=candle_time.date(),
            triggered_at=candle_time,
            trigger_level=TriggerLevel.SUPPORT,
            trigger_price=1899.5,
            analysis_id=analysis_id,
        )

        ai_analyzer = FakeAIAnalyzer([])
        client = MagicMock(spec=Mt5AccountClient)
        # Touch support again
        client.get_current_tick.return_value = _tick(1899.5, 1899.7, candle_time)
        client.get_candles.return_value = _m5_candles(candle_time)

        executor = MagicMock(spec=MultiAccountExecutor)
        executor.get_symbol_specs.return_value = {i: _spec() for i in (1, 2, 3, 4)}
        executor.execute_all.return_value = [
            ExecutionResult(account_id=i, success=True, order_ticket=2000 + i, entry_price=1899.5)
            for i in (1, 2, 3, 4)
        ]

        engine, executor, _, settings = _make_engine(
            repos, ai_analyzer, executor=executor, market_data_client=client
        )
        # Configure max_opportunities_per_day to 2
        settings.trading.max_opportunities_per_day = 2

        # This should execute another opportunity because the limit is 2
        engine.run_once()

        assert repos.opportunity.has_opportunity_today(candle_time.date(), limit=2) is True


class TestRunForever:
    def test_continues_after_exception(self, repos, monkeypatch) -> None:
        ai_analyzer = FakeAIAnalyzer([])
        engine, _, _, _ = _make_engine(repos, ai_analyzer)

        calls: list[int] = []

        def fake_run_once() -> None:
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("boom")

        engine.run_once = fake_run_once  # type: ignore[method-assign]
        monkeypatch.setattr("xauusd_bot.engine.trading_engine.time.sleep", lambda seconds: None)

        engine.run_forever(max_iterations=3)

        assert len(calls) == 3
