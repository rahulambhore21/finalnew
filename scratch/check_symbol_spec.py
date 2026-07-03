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

info = mt5.account_info()
if info is not None:
    print("=== MT5 Account Info ===")
    for k, v in info._asdict().items():
        print(f"{k}: {v}")
else:
    print("Account info not found")

mt5.shutdown()
