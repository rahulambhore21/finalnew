"""SQLite schema for the trading bot's persistent state."""

from __future__ import annotations

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS market_analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        requested_at TEXT NOT NULL,
        candle_time TEXT NOT NULL,
        symbol TEXT NOT NULL,
        support REAL NOT NULL,
        resistance REAL NOT NULL,
        confidence REAL NOT NULL,
        reason TEXT NOT NULL,
        model TEXT NOT NULL,
        raw_response TEXT,
        created_at TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_opportunities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trading_date TEXT NOT NULL,
        triggered_at TEXT NOT NULL,
        trigger_level TEXT NOT NULL CHECK (trigger_level IN ('SUPPORT', 'RESISTANCE')),
        trigger_price REAL NOT NULL,
        analysis_id INTEGER NOT NULL REFERENCES market_analyses(id),
        created_at TEXT NOT NULL DEFAULT (STRFTIME('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trades (
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
    """,
    "CREATE INDEX IF NOT EXISTS idx_trades_opportunity ON trades(opportunity_id)",
)
