import os
import MetaTrader5 as mt5
from dotenv import load_dotenv
from decimal import Decimal
from xauusd_bot.domain.models import SymbolSpec, RiskParameters
from xauusd_bot.config.settings import AccountCredentials
from xauusd_bot.risk.risk_engine import RiskEngine

load_dotenv()

login = int(os.environ["MT5_ACCOUNT_1_LOGIN"])
password = os.environ["MT5_ACCOUNT_1_PASSWORD"]
server = os.environ["MT5_ACCOUNT_1_SERVER"]
path = os.environ["MT5_ACCOUNT_1_TERMINAL_PATH"]

if not mt5.initialize(path=path, login=login, password=password, server=server):
    print("Initialize failed")
    exit(1)

info = mt5.symbol_info("XAUUSD")
spec = SymbolSpec(
    symbol="XAUUSD",
    digits=info.digits,
    point=info.point,
    tick_size=info.trade_tick_size,
    tick_value=info.trade_tick_value,
    contract_size=info.trade_contract_size,
    volume_min=info.volume_min,
    volume_max=info.volume_max,
    volume_step=info.volume_step,
)

print("Symbol Spec:")
print(spec)

risk_params = RiskParameters(
    risk_usd=50.0,
    reward_usd=150.0,
    lot_min=0.05,
    lot_max=0.10,
    preferred_lot_size=0.05,
)

engine = RiskEngine(risk_params)
accounts = (
    AccountCredentials(account_id=1, side="BUY", login=login, password=password, server=server, terminal_path=path),
)

try:
    plans = engine.compute_trade_plans(accounts, {1: spec}, entry_price=info.ask)
    print("Plans computed successfully:")
    for plan in plans:
        print(plan)
except Exception as e:
    print("Error computing plans:")
    import traceback
    traceback.print_exc()

mt5.shutdown()
