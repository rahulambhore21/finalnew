"""Shared enumerations used across trading domain models."""

from __future__ import annotations

from enum import Enum


class Timeframe(str, Enum):
    M5 = "M5"
    M15 = "M15"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TriggerLevel(str, Enum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class TradeStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_MANUAL = "CLOSED_MANUAL"
    FAILED = "FAILED"
