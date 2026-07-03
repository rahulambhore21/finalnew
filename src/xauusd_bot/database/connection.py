"""SQLite connection management and schema initialization."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from xauusd_bot.database.schema import SCHEMA_STATEMENTS

logger = logging.getLogger(__name__)


def get_connection(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL journal mode and foreign keys enabled.

    Creates the parent directory if it doesn't exist. Rows are returned as
    ``sqlite3.Row`` for dict-like access by column name.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Idempotently create all tables and apply migrations if necessary."""
    # Check if we need to migrate daily_opportunities to drop UNIQUE on trading_date
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='daily_opportunities'"
    ).fetchone()
    if row is not None:
        table_sql = row["sql"]
        if "unique" in table_sql.lower() and "trading_date" in table_sql.lower():
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.execute("BEGIN TRANSACTION")
                conn.execute("ALTER TABLE daily_opportunities RENAME TO daily_opportunities_old")
                conn.execute("""
                    CREATE TABLE daily_opportunities (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trading_date TEXT NOT NULL,
                        triggered_at TEXT NOT NULL,
                        trigger_level TEXT NOT NULL CHECK (trigger_level IN ('SUPPORT', 'RESISTANCE')),
                        trigger_price REAL NOT NULL,
                        analysis_id INTEGER NOT NULL REFERENCES market_analyses(id),
                        created_at TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    )
                """)
                conn.execute("""
                    INSERT INTO daily_opportunities (id, trading_date, triggered_at, trigger_level, trigger_price, analysis_id, created_at)
                    SELECT id, trading_date, triggered_at, trigger_level, trigger_price, analysis_id, created_at FROM daily_opportunities_old
                """)
                conn.execute("DROP TABLE daily_opportunities_old")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")

    # Check if the trades table has a broken foreign key reference pointing to daily_opportunities_old
    trades_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='trades'"
    ).fetchone()
    if trades_row is not None:
        trades_sql = trades_row["sql"]
        if "daily_opportunities_old" in trades_sql:
            logger.info("Migrating trades table to restore reference to daily_opportunities")
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.execute("BEGIN TRANSACTION")
                conn.execute("ALTER TABLE trades RENAME TO trades_old")
                conn.execute("""
                    CREATE TABLE trades (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        opportunity_id INTEGER NOT NULL REFERENCES daily_opportunities(id),
                        account_id INTEGER NOT NULL,
                        side TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                        symbol TEXT NOT NULL,
                        lot_size REAL NOT NULL,
                        entry_price REAL,
                        stop_loss REAL NOT NULL,
                        take_profit REAL NOT NULL,
                        mt5_order_ticket INTEGER,
                        status TEXT NOT NULL CHECK (
                            status IN ('PENDING', 'OPEN', 'CLOSED_TP', 'CLOSED_SL', 'CLOSED_MANUAL', 'FAILED')
                        ) DEFAULT 'PENDING',
                        opened_at TEXT,
                        closed_at TEXT,
                        close_price REAL,
                        profit_usd REAL,
                        error_message TEXT,
                        created_at TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    )
                """)
                columns_row = conn.execute("PRAGMA table_info(trades_old)").fetchall()
                column_names = [col["name"] for col in columns_row]
                cols_str = ", ".join(column_names)
                conn.execute(f"""
                    INSERT INTO trades ({cols_str})
                    SELECT {cols_str} FROM trades_old
                """)
                conn.execute("DROP TABLE trades_old")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.execute("PRAGMA foreign_keys = ON")

    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()
