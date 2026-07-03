import MetaTrader5 as mt5
from dotenv import load_dotenv
import os

load_dotenv()

login = int(os.environ["MT5_ACCOUNT_1_LOGIN"])
password = os.environ["MT5_ACCOUNT_1_PASSWORD"]
server = os.environ["MT5_ACCOUNT_1_SERVER"]
path = os.environ["MT5_ACCOUNT_1_TERMINAL_PATH"]

if not mt5.initialize(path=path, login=login, password=password, server=server):
    print("Initialize failed")
    exit(1)

# Calculate profit for 1 lot, entry 4170.00, exit 4170.01 (BUY)
profit_buy = mt5.order_calc_profit(
    mt5.ORDER_TYPE_BUY,
    "XAUUSD",
    1.0,
    4170.00,
    4170.01
)

# Calculate profit for 1 lot, entry 4170.00, exit 4170.01 (SELL)
profit_sell = mt5.order_calc_profit(
    mt5.ORDER_TYPE_SELL,
    "XAUUSD",
    1.0,
    4170.00,
    4170.01
)

print(f"Profit BUY for 1 tick (0.01): {profit_buy} GBP")
print(f"Profit SELL for 1 tick (0.01): {profit_sell} GBP")

# Get symbol info
info = mt5.symbol_info("XAUUSD")
print(f"Symbol trade_tick_value: {info.trade_tick_value}")
print(f"Symbol trade_tick_size: {info.trade_tick_size}")
print(f"Symbol trade_contract_size: {info.trade_contract_size}")

mt5.shutdown()
