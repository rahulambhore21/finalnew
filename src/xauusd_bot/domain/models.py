"""Domain objects shared across the AI, Risk, MT5, and Trading Engine modules.

All are pydantic models so data crossing module boundaries (especially AI
responses) is validated rather than trusted as-is, per CLAUDE.md's "never
trust AI/MT5 responses without validation" rule.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from xauusd_bot.domain.enums import OrderSide, TradeStatus, TriggerLevel


class Candle(BaseModel):
    """One OHLCV bar for a given timeframe."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class Tick(BaseModel):
    """A live bid/ask quote. BUY fills/triggers compare against ask, SELL against bid."""

    bid: float
    ask: float
    time: datetime


class MarketAnalysis(BaseModel):
    """Validated result of one AI market analysis call."""

    support: float
    resistance: float
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    analyzed_at: datetime
    candle_time: datetime
    symbol: str
    model: str

    @model_validator(mode="after")
    def resistance_above_support(self) -> MarketAnalysis:
        if self.resistance <= self.support:
            raise ValueError("resistance must be greater than support")
        return self


class TradeSignal(BaseModel):
    """A live price touching the latest Support or Resistance level."""

    triggered_at: datetime
    trigger_level: TriggerLevel
    trigger_price: float
    analysis: MarketAnalysis


class SymbolSpec(BaseModel):
    """MT5 symbol specification needed for lot/SL/TP calculation."""

    symbol: str
    digits: int
    point: float
    tick_size: float
    tick_value: float
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float


class RiskParameters(BaseModel):
    """User-configured dollar-based risk management targets."""

    risk_usd: float
    reward_usd: float
    lot_min: float
    lot_max: float
    preferred_lot_size: float


class TradePlan(BaseModel):
    """Fully calculated order parameters for one account, ready to send to MT5."""

    account_id: int
    side: OrderSide
    symbol: str
    lot_size: float
    stop_loss: float
    take_profit: float
    estimated_entry_price: float


class ExecutionResult(BaseModel):
    """Outcome of sending one TradePlan to MT5.

    ``rolled_back`` is True only when this account's order originally
    succeeded but was subsequently closed because a sibling account's order
    in the same opportunity failed (see MultiAccountExecutor). It is False
    both for an original failure and for a case where rollback itself could
    not be completed (that case is success=False with rolled_back=False and
    an error_message flagging manual intervention, since the position's
    true state is unknown/likely still open).
    """

    account_id: int
    success: bool
    order_ticket: int | None = None
    entry_price: float | None = None
    error_message: str | None = None
    rolled_back: bool = False


class TradeResult(BaseModel):
    """Final outcome of a trade once it has closed (TP/SL/manual)."""

    trade_id: int
    account_id: int
    close_price: float
    profit_usd: float
    status: TradeStatus


class OpenTradeQuery(BaseModel):
    """Identifies one OPEN trade row's MT5 ticket/account for a monitoring pass."""

    trade_id: int
    account_id: int
    symbol: str
    mt5_order_ticket: int
    stop_loss: float
    take_profit: float


class PositionCheckResult(BaseModel):
    """Outcome of checking one MT5 position/ticket's live status.

    ``still_open=True`` and ``close_status=None`` (with ``still_open=False``,
    meaning "no exit deal found yet") are both treated identically by
    callers: no DB update this cycle, retry next cycle.
    """

    ticket: int
    still_open: bool
    close_status: TradeStatus | None = None
    close_price: float | None = None
    profit_usd: float | None = None
    closed_at: datetime | None = None
