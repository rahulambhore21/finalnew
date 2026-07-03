import sqlite3
from xauusd_bot.database.connection import init_db

conn = sqlite3.connect("data/trading.db")
conn.row_factory = sqlite3.Row
init_db(conn)
for row in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
    print(f"Table: {row['name']}")
    print(row['sql'])
    print("-" * 50)
conn.close()
