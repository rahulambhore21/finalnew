"""Thin adapter around the MetaTrader5 package for a single account/terminal.

Business logic (risk calculation, trade decisions) must never live here —
this module only translates between our domain objects and MT5 API calls.
"""

from __future__ import annotations

from datetime import datetime, timezone

import MetaTrader5 as mt5

from xauusd_bot.config.settings import AccountCredentials
from xauusd_bot.domain.enums import OrderSide, Timeframe, TradeStatus
from xauusd_bot.domain.models import (
    Candle,
    ExecutionResult,
    PositionCheckResult,
    SymbolSpec,
    Tick,
    TradePlan,
)
from xauusd_bot.logging_setup.logger import get_logger
from xauusd_bot.mt5_adapter.errors import Mt5ConnectionError, Mt5OrderError

logger = get_logger(__name__)

_TIMEFRAME_MAP: dict[Timeframe, int] = {
    Timeframe.M5: mt5.TIMEFRAME_M5,
    Timeframe.M15: mt5.TIMEFRAME_M15,
}

# MQL5's ENUM_SYMBOL_TRADE_EXECUTION filling-mode bitmask values (from the
# official MQL5 SymbolInfoInteger(SYMBOL_FILLING_MODE) documentation). The
# MetaTrader5 Python package does not expose SYMBOL_FILLING_* constants —
# only ORDER_FILLING_* exist, which is a *different* enum used in the order
# request itself. These are stable wire-protocol constants, not tunable
# configuration.
_SYMBOL_FILLING_FOK = 1
_SYMBOL_FILLING_IOC = 2

_CONNECT_TIMEOUT_MS = 10_000

# Generous slippage tolerance (in symbol points) used only to classify a
# closed position's exit price as TP vs SL vs a manual/other close — not a
# tunable risk parameter, just a classification heuristic.
_CLOSE_PRICE_TOLERANCE_POINTS = 20


class Mt5AccountClient:
    """Wraps one MT5 terminal/account.

    Only one client may be connected (``mt5.initialize``'d) per process at a
    time — the ``MetaTrader5`` package attaches to a single running terminal.
    Callers must bracket ``connect()``/``disconnect()`` per account.
    """

    def __init__(
        self,
        credentials: AccountCredentials,
        deviation_points: int,
        magic_number: int,
    ) -> None:
        self._credentials = credentials
        self._deviation_points = deviation_points
        self._magic_number = magic_number

    @property
    def account_id(self) -> int:
        """This client's account_id, used by MultiAccountExecutor to match plans to clients."""
        return self._credentials.account_id

    # Class-level tracking of the currently initialized account connection in MT5
    _active_account_id: int | None = None

    def connect(self) -> None:
        """Initialize (or re-attach to) this account's MT5 terminal and verify identity.

        Passes login/password/server explicitly to ``mt5.initialize()`` rather
        than relying on the terminal's own already-logged-in state, so a
        terminal restart is recovered from automatically. Verifies via
        ``mt5.account_info()`` that the resulting session belongs to the
        configured login — never trust the MT5 response without validation.

        Raises:
            Mt5ConnectionError: if initialize() fails, account_info() is
                unavailable, or the logged-in account does not match
                credentials.login.
        """
        credentials = self._credentials

        # If another account is active, shut it down first
        if (
            Mt5AccountClient._active_account_id is not None
            and Mt5AccountClient._active_account_id != credentials.account_id
        ):
            logger.info(
                "Switching MT5 terminal connection from account_id=%s to account_id=%s",
                Mt5AccountClient._active_account_id,
                credentials.account_id,
            )
            mt5.shutdown()
            Mt5AccountClient._active_account_id = None

        # If this account is already active, verify the connection is still alive
        if Mt5AccountClient._active_account_id == credentials.account_id:
            account_info = mt5.account_info()
            if account_info is not None and account_info.login == credentials.login:
                logger.debug(
                    "Reusing active MT5 terminal connection for account_id=%s",
                    credentials.account_id,
                )
                return
            # If account_info is None or login does not match, connection is dead/out of sync
            logger.warning(
                "MT5 connection for account_id=%s is dead or out of sync; reconnecting",
                credentials.account_id,
            )
            mt5.shutdown()
            Mt5AccountClient._active_account_id = None

        initialized = mt5.initialize(
            path=credentials.terminal_path,
            login=credentials.login,
            password=credentials.password.get_secret_value(),
            server=credentials.server,
            timeout=_CONNECT_TIMEOUT_MS,
        )
        if not initialized:
            code, message = mt5.last_error()
            raise Mt5ConnectionError(
                f"mt5.initialize() failed for account_id={credentials.account_id} "
                f"login={credentials.login}: ({code}) {message}"
            )

        account_info = mt5.account_info()
        if account_info is None:
            mt5.shutdown()
            raise Mt5ConnectionError(
                f"mt5.account_info() returned None after initialize for "
                f"account_id={credentials.account_id}"
            )
        if account_info.login != credentials.login:
            mt5.shutdown()
            raise Mt5ConnectionError(
                f"Logged-in account {account_info.login} does not match configured "
                f"login {credentials.login} for account_id={credentials.account_id}"
            )

        Mt5AccountClient._active_account_id = credentials.account_id
        logger.info(
            "Connected to MT5 terminal for account_id=%s (login=%s, server=%s)",
            credentials.account_id,
            credentials.login,
            credentials.server,
        )

    def disconnect(self) -> None:
        """Shut down this process's MT5 terminal connection.

        In the persistent connection caching model, disconnect() does nothing
        to keep the connection alive across cycles. Use force_disconnect()
        if you explicitly want to shut down.
        """
        pass

    def force_disconnect(self) -> None:
        """Forcefully shut down the connection and clear the active account tracking."""
        mt5.shutdown()
        if Mt5AccountClient._active_account_id == self._credentials.account_id:
            Mt5AccountClient._active_account_id = None
        logger.info("Forcefully disconnected MT5 terminal for account_id=%s", self._credentials.account_id)

    def get_symbol_spec(self, symbol: str) -> SymbolSpec:
        """Fetch and validate this account's MT5 symbol specification for ``symbol``.

        If the symbol is not yet visible in Market Watch, attempts
        ``symbol_select(symbol, True)`` and re-fetches once before giving up.

        Raises:
            Mt5ConnectionError: if the symbol is unavailable even after
                selecting it.
        """
        info = mt5.symbol_info(symbol)
        if info is None:
            raise Mt5ConnectionError(
                f"mt5.symbol_info({symbol!r}) returned None "
                f"(account_id={self._credentials.account_id})"
            )
        if not info.visible:
            if not mt5.symbol_select(symbol, True):
                raise Mt5ConnectionError(
                    f"mt5.symbol_select({symbol!r}, True) failed "
                    f"(account_id={self._credentials.account_id})"
                )
            info = mt5.symbol_info(symbol)
            if info is None or not info.visible:
                raise Mt5ConnectionError(
                    f"Symbol {symbol!r} still unavailable after symbol_select "
                    f"(account_id={self._credentials.account_id})"
                )

        tick_value = info.trade_tick_value
        # For symbols whose profit currency is USD (such as XAUUSD), the tick value
        # in USD is contract_size * tick_size. Since our config targets (risk_usd,
        # reward_usd) are in USD, we must calculate SL/TP price distances in USD
        # by passing the USD-denominated tick value to the RiskEngine. This resolves
        # unit errors when the account deposit currency is not USD (e.g. GBP) and
        # protects against incorrect or stale trade_tick_value responses from the broker.
        if getattr(info, "currency_profit", None) == "USD":
            tick_value = info.trade_contract_size * info.trade_tick_size

        return SymbolSpec(
            symbol=symbol,
            digits=info.digits,
            point=info.point,
            tick_size=info.trade_tick_size,
            tick_value=tick_value,
            contract_size=info.trade_contract_size,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
        )

    def get_candles(self, symbol: str, timeframe: Timeframe, count: int) -> list[Candle]:
        """Fetch the most recent ``count`` closed+forming bars for ``symbol``/``timeframe``.

        Uses ``tick_volume`` (not ``real_volume``), since real_volume is
        typically 0 for CFD/forex/gold symbols on MT5 and would silently
        produce meaningless zero-volume candles.

        Raises:
            Mt5ConnectionError: if MT5 returns None or an empty array.
        """
        mt5_timeframe = _TIMEFRAME_MAP[timeframe]
        rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, count)
        if rates is None or len(rates) == 0:
            raise Mt5ConnectionError(
                f"mt5.copy_rates_from_pos returned no data for symbol={symbol!r} "
                f"timeframe={timeframe} (account_id={self._credentials.account_id})"
            )
        return [
            Candle(
                time=datetime.fromtimestamp(int(row["time"]), tz=timezone.utc),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["tick_volume"]),
            )
            for row in rates
        ]

    def get_current_tick(self, symbol: str) -> Tick:
        """Fetch the current bid/ask quote for ``symbol``.

        Only account 1's client is expected to be used for this by the
        TradingEngine (shared market data source) — see MultiAccountExecutor
        module docstring for rationale.

        Raises:
            Mt5ConnectionError: if MT5 returns None or a non-positive bid/ask.
        """
        tick = mt5.symbol_info_tick(symbol)
        if tick is None or tick.bid <= 0 or tick.ask <= 0:
            raise Mt5ConnectionError(
                f"mt5.symbol_info_tick({symbol!r}) returned an invalid tick "
                f"(account_id={self._credentials.account_id})"
            )
        return Tick(
            bid=tick.bid,
            ask=tick.ask,
            time=datetime.fromtimestamp(tick.time, tz=timezone.utc),
        )

    def send_market_order(self, plan: TradePlan) -> ExecutionResult:
        """Send a market order for ``plan`` on this account.

        Raises:
            Mt5ConnectionError: if the current tick or symbol spec cannot be
                fetched.
            Mt5OrderError: if no supported filling mode exists, order_send()
                returns None, or the order is rejected (retcode != DONE).
        """
        return self._send_deal(
            symbol=plan.symbol,
            side=plan.side,
            volume=plan.lot_size,
            sl=plan.stop_loss,
            tp=plan.take_profit,
            account_id=plan.account_id,
            comment="xauusd-bot open",
            position_ticket=None,
        )

    def close_position(
        self,
        account_id: int,
        symbol: str,
        side: OrderSide,
        volume: float,
        position_ticket: int,
    ) -> ExecutionResult:
        """Close an open position by sending an opposite-direction deal referencing it.

        ``side`` is the ORIGINAL opening side (e.g. BUY); the closing deal
        sent is the opposite (SELL). Used only for rollback after a partial
        multi-account execution failure (see MultiAccountExecutor).

        Raises:
            Mt5ConnectionError: if the current tick cannot be fetched.
            Mt5OrderError: if the close order is rejected.
        """
        opposite_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        return self._send_deal(
            symbol=symbol,
            side=opposite_side,
            volume=volume,
            sl=0.0,
            tp=0.0,
            account_id=account_id,
            comment="xauusd-bot rollback-close",
            position_ticket=position_ticket,
        )

    def get_position_status(
        self, ticket: int, symbol: str, stop_loss: float, take_profit: float
    ) -> PositionCheckResult:
        """Check whether position ``ticket`` is still open, and if not, classify its close.

        Checks ``mt5.positions_get(ticket=ticket)`` first; a non-empty
        result means still open. Otherwise searches
        ``mt5.history_deals_get(position=ticket)`` for the exit deal
        (``entry == mt5.DEAL_ENTRY_OUT``) and classifies the close reason by
        comparing its price to stop_loss/take_profit within
        ``_CLOSE_PRICE_TOLERANCE_POINTS`` * the symbol's point size, falling
        back to CLOSED_MANUAL if it matches neither (e.g. an operator close
        or a broker-side stop-out at a different price).

        If no exit deal is found at all (not yet visible in MT5 history),
        returns close_status=None (undetermined) rather than guessing —
        callers must retry on the next cycle.

        Raises:
            Mt5ConnectionError: if positions_get()/history_deals_get()
                itself returns None (an API-level failure, distinct from a
                legitimate empty/no-match result).
        """
        positions = mt5.positions_get(ticket=ticket)
        if positions is None:
            code, message = mt5.last_error()
            raise Mt5ConnectionError(
                f"mt5.positions_get(ticket={ticket}) returned None "
                f"(account_id={self._credentials.account_id}): ({code}) {message}"
            )
        if len(positions) > 0:
            return PositionCheckResult(ticket=ticket, still_open=True)

        deals = mt5.history_deals_get(position=ticket)
        if deals is None:
            code, message = mt5.last_error()
            raise Mt5ConnectionError(
                f"mt5.history_deals_get(position={ticket}) returned None "
                f"(account_id={self._credentials.account_id}): ({code}) {message}"
            )

        exit_deals = [deal for deal in deals if deal.entry == mt5.DEAL_ENTRY_OUT]
        if not exit_deals:
            logger.warning(
                "Ticket=%s (account_id=%s) has no open position and no closing "
                "deal in history yet; undetermined this cycle",
                ticket,
                self._credentials.account_id,
            )
            return PositionCheckResult(ticket=ticket, still_open=False, close_status=None)

        closing_deal = max(exit_deals, key=lambda deal: deal.time)
        close_price = float(closing_deal.price)
        profit_ac = float(closing_deal.profit)
        closed_at = datetime.fromtimestamp(closing_deal.time, tz=timezone.utc)

        info = mt5.symbol_info(symbol)
        point = info.point if info is not None else 0.01

        profit_usd = profit_ac
        if info is not None and getattr(info, "currency_profit", None) == "USD":
            # For USD profit currency, if the account currency is not USD, we convert
            # the profit from account currency to USD using a 1-tick order profit query.
            # A 1-tick move of 1 lot on XAUUSD is exactly 1.0 USD of profit.
            calc_profit = mt5.order_calc_profit(
                mt5.ORDER_TYPE_BUY,
                symbol,
                1.0,
                close_price,
                close_price + point
            )
            # Only perform conversion if calc_profit is a valid float/int and not a MagicMock (for unit tests)
            if isinstance(calc_profit, (int, float)) and calc_profit > 0:
                conv_factor = 1.0 / calc_profit
                profit_usd = profit_ac * conv_factor
                logger.info(
                    "Converted closed deal profit from account currency (%s) to USD: "
                    "%s -> %s (conv_factor=%s)",
                    getattr(info, "currency_profit", "USD"),
                    profit_ac,
                    profit_usd,
                    conv_factor
                )
        tolerance = point * _CLOSE_PRICE_TOLERANCE_POINTS

        if abs(close_price - take_profit) <= tolerance:
            close_status = TradeStatus.CLOSED_TP
        elif abs(close_price - stop_loss) <= tolerance:
            close_status = TradeStatus.CLOSED_SL
        else:
            close_status = TradeStatus.CLOSED_MANUAL

        logger.info(
            "Position closed for ticket=%s (account_id=%s): close_price=%s "
            "profit_usd=%s classified=%s",
            ticket,
            self._credentials.account_id,
            close_price,
            profit_usd,
            close_status,
        )
        return PositionCheckResult(
            ticket=ticket,
            still_open=False,
            close_status=close_status,
            close_price=close_price,
            profit_usd=profit_usd,
            closed_at=closed_at,
        )

    def _resolve_filling_mode(self, symbol: str) -> int:
        """Pick a filling mode this symbol supports, preferring IOC over FOK.

        Raises:
            Mt5OrderError: if the symbol's filling_mode bitmask supports
                neither IOC nor FOK.
        """
        info = mt5.symbol_info(symbol)
        if info is None:
            raise Mt5OrderError(
                f"mt5.symbol_info({symbol!r}) unavailable while resolving filling mode"
            )
        bitmask = info.filling_mode
        if bitmask & _SYMBOL_FILLING_IOC:
            return mt5.ORDER_FILLING_IOC
        if bitmask & _SYMBOL_FILLING_FOK:
            return mt5.ORDER_FILLING_FOK
        raise Mt5OrderError(
            f"Symbol {symbol!r} supports neither IOC nor FOK filling "
            f"(filling_mode bitmask={bitmask})"
        )

    def _send_deal(
        self,
        *,
        symbol: str,
        side: OrderSide,
        volume: float,
        sl: float,
        tp: float,
        account_id: int,
        comment: str,
        position_ticket: int | None,
    ) -> ExecutionResult:
        tick = self.get_current_tick(symbol)
        order_type = mt5.ORDER_TYPE_BUY if side == OrderSide.BUY else mt5.ORDER_TYPE_SELL
        price = tick.ask if side == OrderSide.BUY else tick.bid
        filling_mode = self._resolve_filling_mode(symbol)

        request: dict[str, object] = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": self._deviation_points,
            "magic": self._magic_number,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }
        if position_ticket is not None:
            request["position"] = position_ticket

        result = mt5.order_send(request)
        if result is None:
            code, message = mt5.last_error()
            raise Mt5OrderError(
                f"mt5.order_send returned None for account_id={account_id}: "
                f"({code}) {message}"
            )
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            raise Mt5OrderError(
                f"Order rejected for account_id={account_id}: "
                f"retcode={result.retcode} comment={result.comment!r}"
            )

        logger.info(
            "Order executed for account_id=%s symbol=%s side=%s volume=%s "
            "ticket=%s price=%s",
            account_id,
            symbol,
            side,
            volume,
            result.order,
            result.price,
        )
        return ExecutionResult(
            account_id=account_id,
            success=True,
            order_ticket=result.order,
            entry_price=result.price,
        )
