import sqlite3

conn = sqlite3.connect("data/trading.db")
conn.row_factory = sqlite3.Row

print("=== OPPORTUNITIES ===")
opps = conn.execute("SELECT * FROM daily_opportunities ORDER BY id DESC").fetchall()
for opp in opps:
    print(dict(opp))

print("\n=== TRADES ===")
trades = conn.execute("SELECT * FROM trades ORDER BY id DESC").fetchall()
for trade in trades:
    print(dict(trade))

conn.close()
