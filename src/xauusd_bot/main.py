"""Application entry point.

Wires configuration, logging, the database, and the AI/MT5/Risk/Engine
modules together, then starts the trading engine's poll loop.
"""

from __future__ import annotations

from xauusd_bot.ai.analyzer import AIAnalyzer
from xauusd_bot.config.settings import load_settings
from xauusd_bot.database.connection import get_connection, init_db
from xauusd_bot.database.repositories import (
    AnalysisRepository,
    OpportunityRepository,
    TradeRepository,
)
from xauusd_bot.domain.models import RiskParameters
from xauusd_bot.engine.trading_engine import TradingEngine
from xauusd_bot.logging_setup.logger import configure_logging, get_logger
from xauusd_bot.mt5_adapter.client import Mt5AccountClient
from xauusd_bot.mt5_adapter.executor import MultiAccountExecutor
from xauusd_bot.risk.risk_engine import RiskEngine


def main() -> None:
    """Load configuration, set up logging and the database, construct the
    trading engine and its collaborators, and start the trading loop."""
    settings = load_settings()
    configure_logging(
        log_dir=settings.logging.dir,
        level=settings.logging.level,
        max_bytes=settings.logging.max_bytes,
        backup_count=settings.logging.backup_count,
    )
    logger = get_logger(__name__)
    logger.info("XAUUSD trading bot starting up")

    conn = get_connection(settings.database.path)
    init_db(conn)
    logger.info("Database schema initialized at %s", settings.database.path)

    opportunity_repo = OpportunityRepository(conn)
    analysis_repo = AnalysisRepository(conn)
    trade_repo = TradeRepository(conn)

    ai_analyzer = AIAnalyzer(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.ai.model,
        timeout_seconds=settings.ai.timeout_seconds,
        max_retries=settings.ai.max_retries,
        temperature=settings.ai.temperature,
        lot_min=settings.trading.lot_min,
        lot_max=settings.trading.lot_max,
    )
    risk_engine = RiskEngine(
        RiskParameters(
            risk_usd=settings.trading.risk_usd,
            reward_usd=settings.trading.reward_usd,
            lot_min=settings.trading.lot_min,
            lot_max=settings.trading.lot_max,
            preferred_lot_size=settings.trading.preferred_lot_size,
        )
    )
    clients = [
        Mt5AccountClient(
            account,
            deviation_points=settings.trading.deviation_points,
            magic_number=settings.trading.magic_number,
        )
        for account in settings.accounts
    ]
    executor = MultiAccountExecutor(clients)
    market_data_client = next(client for client in clients if client.account_id == 1)

    engine = TradingEngine(
        settings=settings,
        ai_analyzer=ai_analyzer,
        risk_engine=risk_engine,
        executor=executor,
        market_data_client=market_data_client,
        opportunity_repo=opportunity_repo,
        analysis_repo=analysis_repo,
        trade_repo=trade_repo,
    )
    logger.info(
        "Trading engine constructed; starting run_forever() loop "
        "(poll_interval_seconds=%s)",
        settings.trading.poll_interval_seconds,
    )
    try:
        engine.run_forever()
    finally:
        logger.info("Trading engine stopping; shutting down MT5 connections")
        import MetaTrader5 as mt5
        mt5.shutdown()


if __name__ == "__main__":
    main()
