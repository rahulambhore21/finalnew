"""Exceptions raised by the risk engine module."""

from __future__ import annotations


class RiskCalculationError(Exception):
    """Raised when no lot size within the configured bounds satisfies every
    account's volume constraints."""
