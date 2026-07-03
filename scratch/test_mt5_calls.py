import time
import MetaTrader5 as mt5
from dotenv import load_dotenv
import os
from pathlib import Path
from pydantic import SecretStr

load_dotenv()

login = int(os.environ["MT5_ACCOUNT_1_LOGIN"])
password = os.environ["MT5_ACCOUNT_1_PASSWORD"]
server = os.environ["MT5_ACCOUNT_1_SERVER"]
path = os.environ["MT5_ACCOUNT_1_TERMINAL_PATH"]

print("Initializing...")
initialized = mt5.initialize(
    path=path,
    login=login,
    password=password,
    server=server,
    timeout=10000
)
print("Initialized status:", initialized)
if not initialized:
    print("Error:", mt5.last_error())
    exit(1)

try:
    print("Getting account info...")
    info = mt5.account_info()
    print("Account info:", info)

    print("Getting symbol tick...")
    tick = mt5.symbol_info_tick("XAUUSD")
    print("Tick:", tick)

    print("Getting candles...")
    candles = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M5, 0, 50)
    print("Candles fetched:", len(candles) if candles is not None else "None")

    print("Now testing second check (simulating second loop iteration)...")
    print("Calling account_info again...")
    info2 = mt5.account_info()
    print("Account info 2:", info2)

    print("Calling symbol_info_tick again...")
    tick2 = mt5.symbol_info_tick("XAUUSD")
    print("Tick 2:", tick2)

finally:
    print("Shutting down...")
    mt5.shutdown()
    print("Shutdown complete.")
