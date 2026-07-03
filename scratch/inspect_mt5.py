import sys
import os
import traceback

try:
    from dotenv import load_dotenv
    load_dotenv()
    print(".env loaded successfully")
except Exception as e:
    print(f"Error loading dotenv: {e}")
    sys.exit(1)

try:
    import MetaTrader5 as mt5
    print("MetaTrader5 imported successfully")
except Exception as e:
    print(f"Error importing MetaTrader5: {e}")
    sys.exit(1)

def main():
    try:
        login_str = os.getenv("MT5_ACCOUNT_1_LOGIN")
        print(f"MT5_ACCOUNT_1_LOGIN: {login_str}")
        if not login_str:
            print("MT5_ACCOUNT_1_LOGIN is not set or empty")
            return
            
        acc = {
            "id": 1,
            "login": int(login_str),
            "password": os.getenv("MT5_ACCOUNT_1_PASSWORD"),
            "server": os.getenv("MT5_ACCOUNT_1_SERVER"),
            "path": os.getenv("MT5_ACCOUNT_1_TERMINAL_PATH"),
        }
        print(f"Account config: {acc}")
        
        print(f"Connecting to Account 1 (Login: {acc['login']})...")
        initialized = mt5.initialize(
            path=acc["path"],
            login=acc["login"],
            password=acc["password"],
            server=acc["server"],
            timeout=10000
        )
        if not initialized:
            print(f"Failed to initialize: {mt5.last_error()}")
            return
        
        print("Connected! Fetching symbol info...")
        info = mt5.symbol_info("XAUUSD")
        if info is None:
            print("Symbol info not found")
        else:
            print("Symbol Info:")
            print(f"  digits: {info.digits}")
            print(f"  point: {info.point}")
            print(f"  trade_tick_size: {info.trade_tick_size}")
            print(f"  trade_tick_value: {info.trade_tick_value}")
            print(f"  trade_contract_size: {info.trade_contract_size}")
            print(f"  volume_min: {info.volume_min}")
            print(f"  volume_max: {info.volume_max}")
            print(f"  volume_step: {info.volume_step}")
            print(f"  filling_mode: {info.filling_mode}")
            
        mt5.shutdown()
    except Exception as e:
        print("Exception in main:")
        traceback.print_exc()

if __name__ == "__main__":
    main()
