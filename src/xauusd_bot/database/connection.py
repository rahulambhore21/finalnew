"""SQLite connection management and schema initialization."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from xauusd_bot.database.schema import SCHEMA_STATEMENTS


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
    """Idempotently create all tables. No migration framework in V1 —
    single schema version; revisit if the schema needs to evolve after
    production data exists.
    """
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()
