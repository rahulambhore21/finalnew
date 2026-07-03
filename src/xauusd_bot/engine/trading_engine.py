"""Orchestrates monitor -> analyze -> trigger -> execute -> log.

Contains no direct MT5/OpenAI I/O itself — only coordination and the
one-opportunity-per-day / analysis-cadence rules, using dependency-injected
collaborators so each piece stays independently testable.
"""

from __future__ import annotations

import itertools
import time
from datetime import date, datetime, timezone

from xauusd_bot.ai.analyzer import AIAnalyzer
from xauusd_bot.ai.errors import AIAnalysisError
from xauusd_bot.config.settings import Settings
from xauusd_bot.database.repositories import (
    AnalysisRepository,
    OpportunityRepository,
    TradeRepository,
)
from xauusd_bot.domain.enums import Timeframe, TradeStatus, TriggerLevel
from xauusd_bot.domain.models import Candle, MarketAnalysis, OpenTradeQuery, Tick
from xauusd_bot.logging_setup.logger import get_logger
from xauusd_bot.mt5_adapter.client import Mt5AccountClient
from xauusd_bot.mt5_adapter.errors import Mt5ConnectionError
from xauusd_bot.mt5_adapter.executor import MultiAccountExecutor
from xauusd_bot.risk.errors import RiskCalculationError
from xauusd_bot.risk.risk_engine import RiskEngine

logger = get_logger(__name__)


class TradingEngine:
    """Coordinates the full monitor/analyze/trigger/execute/log cycle for XAUUSD."""

    def __init__(
        self,
        settings: Settings,
        ai_analyzer: AIAnalyzer,
        risk_engine: RiskEngine,
        executor: MultiAccountExecutor,
        market_data_client: Mt5AccountClient,
        opportunity_repo: OpportunityRepository,
        analysis_repo: AnalysisRepository,
        trade_repo: TradeRepository,
    ) -> None:
        self._settings = settings
        self._ai_analyzer = ai_analyzer
        self._risk_engine = risk_engine
        self._executor = executor
        self._market_data_client = market_data_client
        self._opportunity_repo = opportunity_repo
        self._analysis_repo = analysis_repo
        self._trade_repo = trade_repo
        self._symbol = settings.trading.symbol
        self._latest_analysis: MarketAnalysis | None = None
        self._latest_analysis_id: int | None = None
        self._last_logged_limit_date: date | None = None

    def run_forever(self, *, max_iterations: int | None = None) -> None:
        """Continuously monitor XAUUSD and execute at most one opportunity per day.

        ``max_iterations`` is None in production (loops forever); tests pass
        a finite number to exercise the loop/sleep/exception-handling
        without hanging.
        """
        logger.info(
            "Trading engine loop starting (poll_interval_seconds=%s)",
            self._settings.trading.poll_interval_seconds,
        )
        iterations = itertools.count() if max_iterations is None else range(max_iterations)
        for _ in iterations:
            try:
                self.run_once()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                logger.exception("Unhandled error in trading engine cycle; continuing after poll interval")
            time.sleep(self._settings.trading.poll_interval_seconds)

    def run_once(self) -> None:
        """Run a single monitor/analyze/trigger/execute cycle. Useful for tests."""
        has_unresolved_open_trades = self._reconcile_open_trades()

        market_data = self._fetch_market_data()
        if market_data is None:
            return
        tick, m5_candles, m15_candles = market_data
        trading_date = tick.time.date()

        limit = self._settings.trading.max_opportunities_per_day
        if self._opportunity_repo.has_opportunity_today(trading_date, limit=limit):
            if self._last_logged_limit_date != trading_date:
                logger.info(
                    "Daily opportunity limit (%s) reached for trading_date=%s. "
                    "The bot will remain idle until the next trading day.",
                    limit,
                    trading_date,
                )
                self._last_logged_limit_date = trading_date
            logger.debug("Opportunity already recorded for trading_date=%s; skipping", trading_date)
            return
        if has_unresolved_open_trades:
            logger.info("Unresolved open trade(s) pending closure; skipping AI analysis and trigger checks")
            return

        latest_candle = m5_candles[-1]
        analysis = self._get_or_refresh_analysis(latest_candle, m5_candles, m15_candles)
        if analysis is None:
            return

        trigger_level, trigger_price = self._check_trigger(analysis, tick)
        if trigger_level is None or trigger_price is None:
            return

        self._execute_opportunity(trading_date, tick.time, analysis, trigger_level, trigger_price)

    def _reconcile_open_trades(self) -> bool:
        """Check every OPEN trade row against MT5 and persist any closures.

        Returns True if anything remains open/undetermined — which must
        block new AI analysis this cycle. This is what correctly extends
        the one-opportunity-per-day rule across a calendar-day boundary:
        CLAUDE.md requires never calling OpenAI while trades are open,
        regardless of what day it is.
        """
        open_rows = self._trade_repo.get_open_trades()
        if not open_rows:
            return False

        queries = [
            OpenTradeQuery(
                trade_id=row["id"],
                account_id=row["account_id"],
                symbol=row["symbol"],
                mt5_order_ticket=row["mt5_order_ticket"],
                stop_loss=row["stop_loss"],
                take_profit=row["take_profit"],
            )
            for row in open_rows
            if row["mt5_order_ticket"] is not None
        ]
        if len(queries) < len(open_rows):
            logger.warning(
                "%d OPEN row(s) missing mt5_order_ticket; treating as unresolved",
                len(open_rows) - len(queries),
            )

        if not queries:
            return True

        logger.info("Reconciling %d open trade(s) against MT5", len(queries))
        statuses = self._executor.check_trade_statuses(queries)

        unresolved = len(queries) < len(open_rows)
        now_iso = datetime.now(tz=timezone.utc).isoformat()
        for query in queries:
            status = statuses.get(query.trade_id)
            if status is None or status.still_open or status.close_status is None:
                unresolved = True
                continue
            self._trade_repo.update_trade_status(
                query.trade_id,
                status.close_status,
                close_price=status.close_price,
                profit_usd=status.profit_usd,
                closed_at=(status.closed_at.isoformat() if status.closed_at else now_iso),
            )
            logger.info(
                "Trade closed: trade_id=%s account_id=%s status=%s close_price=%s profit_usd=%s",
                query.trade_id,
                query.account_id,
                status.close_status,
                status.close_price,
                status.profit_usd,
            )
        return unresolved

    def _fetch_market_data(self) -> tuple[Tick, list[Candle], list[Candle]] | None:
        """Fetch the current tick and M5/M15 candles from the account-1 market-data client.

        Brackets connect()/disconnect() around the fetch every cycle —
        MetaTrader5 supports only one live connection per process, so the
        engine never holds a persistent connection across the polling loop.

        Returns None (skip this cycle) if MT5 access fails.
        """
        try:
            self._market_data_client.connect()
        except Mt5ConnectionError:
            logger.exception("Failed to connect account-1 market-data client; skipping this cycle")
            return None
        try:
            tick = self._market_data_client.get_current_tick(self._symbol)
            m5 = self._market_data_client.get_candles(
                self._symbol, Timeframe.M5, self._settings.ai.m5_candle_count
            )
            m15 = self._market_data_client.get_candles(
                self._symbol, Timeframe.M15, self._settings.ai.m15_candle_count
            )
        except Mt5ConnectionError:
            logger.exception("Failed to fetch market data; skipping this cycle")
            return None
        finally:
            try:
                self._market_data_client.disconnect()
            except Exception:
                logger.exception("Error disconnecting account-1 market-data client (ignored)")
        return tick, m5, m15

    def _get_or_refresh_analysis(
        self, latest_candle: Candle, m5_candles: list[Candle], m15_candles: list[Candle]
    ) -> MarketAnalysis | None:
        """Return the cached analysis, or request a new one on a genuinely new M5 candle.

        Per CLAUDE.md: perform analysis once per new M5 candle, and reuse
        the latest Support/Resistance until the next analysis.
        """
        if self._latest_analysis is None:
            self._latest_analysis = self._analysis_repo.get_latest_analysis()
            if self._latest_analysis is not None:
                self._latest_analysis_id = self._analysis_repo.get_latest_analysis_id()

        is_new_candle = (
            self._latest_analysis is None or latest_candle.time > self._latest_analysis.candle_time
        )
        if not is_new_candle:
            logger.debug(
                "No new M5 candle since candle_time=%s; reusing cached support=%s resistance=%s",
                self._latest_analysis.candle_time,
                self._latest_analysis.support,
                self._latest_analysis.resistance,
            )
            return self._latest_analysis

        logger.info("New M5 candle at %s; requesting AI analysis", latest_candle.time)
        try:
            analysis = self._ai_analyzer.analyze(
                symbol=self._symbol, m5_candles=m5_candles, m15_candles=m15_candles
            )
        except AIAnalysisError:
            logger.exception("AI analysis request failed; will retry next cycle")
            return None

        logger.info(
            "AI analysis received: support=%s resistance=%s confidence=%s lot_size=%s model=%s",
            analysis.support,
            analysis.resistance,
            analysis.confidence,
            analysis.lot_size,
            analysis.model,
        )
        self._latest_analysis_id = self._analysis_repo.insert_analysis(analysis)
        self._latest_analysis = analysis
        return analysis

    def _check_trigger(
        self, analysis: MarketAnalysis, tick: Tick
    ) -> tuple[TriggerLevel | None, float | None]:
        """Check if the live price has reached Support or Resistance.

        Resistance is "reached" when ask rises to meet it (a BUY would
        fill at ask); support is "reached" when bid falls to meet it (a
        SELL would fill at bid) — ties the trigger to an actually
        transactable price rather than a mid-price.
        """
        if tick.ask >= analysis.resistance:
            return TriggerLevel.RESISTANCE, tick.ask
        if tick.bid <= analysis.support:
            return TriggerLevel.SUPPORT, tick.bid
        return None, None

    def _execute_opportunity(
        self,
        trading_date: date,
        triggered_at: datetime,
        analysis: MarketAnalysis,
        trigger_level: TriggerLevel,
        trigger_price: float,
    ) -> None:
        """Compute trade plans, record the opportunity, and execute across all 4 accounts."""
        logger.info(
            "Trigger detected: level=%s price=%s support=%s resistance=%s",
            trigger_level,
            trigger_price,
            analysis.support,
            analysis.resistance,
        )
        accounts = self._settings.accounts
        try:
            symbol_specs = self._executor.get_symbol_specs(self._symbol)
        except Mt5ConnectionError:
            logger.exception("Failed to fetch symbol specs; aborting this opportunity")
            return

        try:
            plans = self._risk_engine.compute_trade_plans(
                accounts,
                symbol_specs,
                entry_price=trigger_price,
                preferred_lot_size=analysis.lot_size,
            )
        except RiskCalculationError as exc:
            logger.warning(
                "Trade skipped (risk guard): trigger_level=%s trigger_price=%s lot_size=%s — %s",
                trigger_level,
                trigger_price,
                analysis.lot_size,
                exc,
            )
            return

        if self._latest_analysis_id is None:
            logger.error("No analysis_id available to record this opportunity; aborting")
            return

        opportunity_id = self._opportunity_repo.record_opportunity(
            trading_date=trading_date,
            triggered_at=triggered_at,
            trigger_level=trigger_level,
            trigger_price=trigger_price,
            analysis_id=self._latest_analysis_id,
        )
        trade_ids = {
            plan.account_id: self._trade_repo.insert_trade(opportunity_id, plan) for plan in plans
        }
        logger.info("Recorded opportunity_id=%s with %d PENDING trades", opportunity_id, len(plans))

        results = self._executor.execute_all(plans)

        now_iso = datetime.now(tz=timezone.utc).isoformat()
        for result in results:
            trade_id = trade_ids[result.account_id]
            if result.success:
                self._trade_repo.update_trade_status(
                    trade_id,
                    TradeStatus.OPEN,
                    entry_price=result.entry_price,
                    mt5_order_ticket=result.order_ticket,
                    opened_at=now_iso,
                )
                logger.info(
                    "Trade opened: trade_id=%s account_id=%s ticket=%s entry_price=%s",
                    trade_id,
                    result.account_id,
                    result.order_ticket,
                    result.entry_price,
                )
            else:
                self._trade_repo.update_trade_status(
                    trade_id, TradeStatus.FAILED, error_message=result.error_message
                )
                logger.error(
                    "Trade failed: trade_id=%s account_id=%s error=%s",
                    trade_id,
                    result.account_id,
                    result.error_message,
                )
