"""Thin data-access layer over the SQLite schema.

Plain ``sqlite3`` with parameterized queries — three tables with simple
relations don't justify an ORM. Each repository takes/returns typed domain
objects or plain Python values, never raw SQL rows, to callers outside this
module.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from xauusd_bot.domain.enums import TradeStatus, TriggerLevel
from xauusd_bot.domain.models import MarketAnalysis, TradePlan


class OpportunityRepository:
    """Enforces and records the one-opportunity-per-trading-day rule."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def has_opportunity_today(self, trading_date: date) -> bool:
        """Return True if an opportunity has already been recorded for this date."""
        row = self._conn.execute(
            "SELECT 1 FROM daily_opportunities WHERE trading_date = ?",
            (trading_date.isoformat(),),
        ).fetchone()
        return row is not None

    def record_opportunity(
        self,
        trading_date: date,
        triggered_at: datetime,
        trigger_level: TriggerLevel,
        trigger_price: float,
        analysis_id: int,
    ) -> int:
        """Record today's opportunity. Raises sqlite3.IntegrityError if one
        already exists for this date (UNIQUE constraint), which should never
        happen if has_opportunity_today() was checked first."""
        cursor = self._conn.execute(
            """
            INSERT INTO daily_opportunities
                (trading_date, triggered_at, trigger_level, trigger_price, analysis_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                trading_date.isoformat(),
                triggered_at.isoformat(),
                trigger_level.value,
                trigger_price,
                analysis_id,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)


class AnalysisRepository:
    """Stores every AI market analysis result for traceability."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert_analysis(self, analysis: MarketAnalysis, raw_response: str | None = None) -> int:
        """Persist a validated MarketAnalysis and return its row id."""
        cursor = self._conn.execute(
            """
            INSERT INTO market_analyses
                (requested_at, candle_time, symbol, support, resistance,
                 confidence, reason, model, raw_response)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                analysis.analyzed_at.isoformat(),
                analysis.candle_time.isoformat(),
                analysis.symbol,
                analysis.support,
                analysis.resistance,
                analysis.confidence,
                analysis.reason,
                analysis.model,
                raw_response,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def get_latest_analysis(self) -> MarketAnalysis | None:
        """Return the most recently requested analysis, or None if none exist."""
        row = self._conn.execute(
            "SELECT * FROM market_analyses ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return MarketAnalysis(
            support=row["support"],
            resistance=row["resistance"],
            confidence=row["confidence"],
            reason=row["reason"],
            analyzed_at=datetime.fromisoformat(row["requested_at"]),
            candle_time=datetime.fromisoformat(row["candle_time"]),
            symbol=row["symbol"],
            model=row["model"],
        )

    def get_latest_analysis_id(self) -> int | None:
        """Row id of the most recent analysis, or None.

        Recovers the FK id for a MarketAnalysis reloaded via
        get_latest_analysis() (which returns only the domain object, not its
        id) after a process restart, so a new daily_opportunities row can
        reference it without re-inserting a duplicate analysis.
        """
        row = self._conn.execute(
            "SELECT id FROM market_analyses ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return int(row["id"]) if row is not None else None


class TradeRepository:
    """Stores per-account trade records for one opportunity."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def insert_trade(self, opportunity_id: int, plan: TradePlan) -> int:
        """Insert a PENDING trade row from a calculated TradePlan and return its row id."""
        cursor = self._conn.execute(
            """
            INSERT INTO trades
                (opportunity_id, account_id, side, symbol, lot_size,
                 stop_loss, take_profit, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')
            """,
            (
                opportunity_id,
                plan.account_id,
                plan.side.value,
                plan.symbol,
                plan.lot_size,
                plan.stop_loss,
                plan.take_profit,
            ),
        )
        self._conn.commit()
        return int(cursor.lastrowid)

    def update_trade_status(self, trade_id: int, status: TradeStatus, **fields: object) -> None:
        """Update a trade's status and any additional columns (e.g. entry_price,
        mt5_order_ticket, opened_at, closed_at, close_price, profit_usd, error_message)."""
        set_clauses = ["status = ?"]
        values: list[object] = [status.value]
        for column, value in fields.items():
            set_clauses.append(f"{column} = ?")
            values.append(value)
        values.append(trade_id)
        self._conn.execute(
            f"UPDATE trades SET {', '.join(set_clauses)} WHERE id = ?",
            values,
        )
        self._conn.commit()

    def get_trades_for_opportunity(self, opportunity_id: int) -> list[dict]:
        """Return all trade rows for one opportunity as plain dicts."""
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE opportunity_id = ?", (opportunity_id,)
        ).fetchall()
        return [dict(row) for row in rows]

    def get_open_trades(self) -> list[dict]:
        """Return all currently-OPEN trade rows across every opportunity.

        Needed by the Trading Engine's monitoring pass, which must catch
        trades opened on a prior day that are still open when a new
        trading day starts.
        """
        rows = self._conn.execute("SELECT * FROM trades WHERE status = 'OPEN'").fetchall()
        return [dict(row) for row in rows]
