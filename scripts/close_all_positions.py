"""Standalone utility: close every open XAUUSD position on all configured MT5 accounts.

This is an emergency/manual-intervention tool, not part of the trading engine's
normal loop. It connects to each of the four configured accounts in turn,
enumerates open positions via ``mt5.positions_get()``, and closes each one with
an opposite-direction market deal via ``Mt5AccountClient.close_position``.

Usage:
    python scripts/close_all_positions.py          # asks for confirmation
    python scripts/close_all_positions.py --yes    # skips confirmation
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import MetaTrader5 as mt5

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xauusd_bot.config.settings import load_settings
from xauusd_bot.domain.enums import OrderSide
from xauusd_bot.logging_setup.logger import configure_logging, get_logger
from xauusd_bot.mt5_adapter.client import Mt5AccountClient
from xauusd_bot.mt5_adapter.errors import Mt5ConnectionError, Mt5OrderError

logger = get_logger(__name__)


def close_all_positions_for_account(client: Mt5AccountClient) -> tuple[int, int]:
    """Close every open position on ``client``'s account.

    Returns:
        A (closed_count, failed_count) tuple.
    """
    account_id = client.account_id
    client.connect()
    try:
        positions = mt5.positions_get()
        if positions is None:
            code, message = mt5.last_error()
            raise Mt5ConnectionError(
                f"mt5.positions_get() returned None (account_id={account_id}): "
                f"({code}) {message}"
            )
        if len(positions) == 0:
            logger.info("No open positions for account_id=%s", account_id)
            return (0, 0)

        closed = 0
        failed = 0
        for position in positions:
            side = OrderSide.BUY if position.type == mt5.POSITION_TYPE_BUY else OrderSide.SELL
            try:
                client.close_position(
                    account_id=account_id,
                    symbol=position.symbol,
                    side=side,
                    volume=position.volume,
                    position_ticket=position.ticket,
                )
                logger.info(
                    "Closed position ticket=%s symbol=%s side=%s volume=%s (account_id=%s)",
                    position.ticket,
                    position.symbol,
                    side,
                    position.volume,
                    account_id,
                )
                closed += 1
            except (Mt5ConnectionError, Mt5OrderError):
                logger.exception(
                    "Failed to close position ticket=%s symbol=%s (account_id=%s)",
                    position.ticket,
                    position.symbol,
                    account_id,
                )
                failed += 1
        return (closed, failed)
    finally:
        client.force_disconnect()


def main() -> None:
    """Load configuration, then close all open positions on every configured account."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the confirmation prompt and close positions immediately.",
    )
    args = parser.parse_args()

    settings = load_settings()
    configure_logging(
        log_dir=settings.logging.dir,
        level=settings.logging.level,
        max_bytes=settings.logging.max_bytes,
        backup_count=settings.logging.backup_count,
    )
    logger.info("close_all_positions script starting up")

    if not args.yes:
        answer = input(
            "This will close ALL open positions on ALL 4 configured MT5 accounts. "
            "Continue? [y/N] "
        )
        if answer.strip().lower() != "y":
            logger.info("Aborted by user; no positions were closed")
            return

    clients = [
        Mt5AccountClient(
            account,
            deviation_points=settings.trading.deviation_points,
            magic_number=settings.trading.magic_number,
        )
        for account in settings.accounts
    ]

    total_closed = 0
    total_failed = 0
    try:
        for client in clients:
            closed, failed = close_all_positions_for_account(client)
            total_closed += closed
            total_failed += failed
    finally:
        mt5.shutdown()

    logger.info(
        "close_all_positions finished: closed=%s failed=%s", total_closed, total_failed
    )


if __name__ == "__main__":
    main()
