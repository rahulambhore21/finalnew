import os
import MetaTrader5 as mt5
from dotenv import load_dotenv

load_dotenv()

accounts = [
    {
        "id": 1,
        "login": int(os.environ["MT5_ACCOUNT_1_LOGIN"]),
        "password": os.environ["MT5_ACCOUNT_1_PASSWORD"],
        "server": os.environ["MT5_ACCOUNT_1_SERVER"],
        "path": os.environ["MT5_ACCOUNT_1_TERMINAL_PATH"],
    },
    {
        "id": 2,
        "login": int(os.environ["MT5_ACCOUNT_2_LOGIN"]),
        "password": os.environ["MT5_ACCOUNT_2_PASSWORD"],
        "server": os.environ["MT5_ACCOUNT_2_SERVER"],
        "path": os.environ["MT5_ACCOUNT_2_TERMINAL_PATH"],
    },
    {
        "id": 3,
        "login": int(os.environ["MT5_ACCOUNT_3_LOGIN"]),
        "password": os.environ["MT5_ACCOUNT_3_PASSWORD"],
        "server": os.environ["MT5_ACCOUNT_3_SERVER"],
        "path": os.environ["MT5_ACCOUNT_3_TERMINAL_PATH"],
    },
    {
        "id": 4,
        "login": int(os.environ["MT5_ACCOUNT_4_LOGIN"]),
        "password": os.environ["MT5_ACCOUNT_4_PASSWORD"],
        "server": os.environ["MT5_ACCOUNT_4_SERVER"],
        "path": os.environ["MT5_ACCOUNT_4_TERMINAL_PATH"],
    },
]

for acc in accounts:
    print(f"\n=== Account {acc['id']} (Login: {acc['login']}) ===")
    if not mt5.initialize(path=acc["path"], login=acc["login"], password=acc["password"], server=acc["server"]):
        print("Initialize failed")
        continue
    
    acc_info = mt5.account_info()
    symbol_info = mt5.symbol_info("XAUUSD")
    
    if acc_info and symbol_info:
        print(f"Account Currency: {acc_info.currency}")
        print(f"Contract Size: {symbol_info.trade_contract_size}")
        print(f"Tick Size: {symbol_info.trade_tick_size}")
        print(f"Tick Value: {symbol_info.trade_tick_value}")
        
        profit = mt5.order_calc_profit(
            mt5.ORDER_TYPE_BUY,
            "XAUUSD",
            1.0,
            4170.00,
            4170.01
        )
        print(f"1-Tick (0.01) Profit for 1 Lot: {profit} {acc_info.currency}")
    else:
        print("Failed to fetch info")
    
    mt5.shutdown()
