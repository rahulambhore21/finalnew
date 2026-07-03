"""Exceptions raised by the MT5 adapter module."""

from __future__ import annotations


class Mt5ConnectionError(Exception):
    """Raised when a terminal connection (initialize/login) fails."""


class Mt5OrderError(Exception):
    """Raised when an order request is rejected or fails to send."""
