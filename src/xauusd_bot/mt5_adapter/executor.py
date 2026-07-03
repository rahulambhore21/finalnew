"""Coordinates near-simultaneous trade execution across all 4 MT5 accounts.

V1 executes sequentially — the MetaTrader5 package supports one active
terminal connection per process, so accounts are connected to in turn. The
execute_all() signature is designed so a later iteration (threads or
separate processes per account) can be swapped in without changing callers.

Partial-failure handling: if any account's order fails, every account that
DID open a position for this opportunity is rolled back (its position
closed), so the bot never leaves an unbalanced set of trades open across the
4 accounts. Every account is attempted before rollback decisions are made,
even if an earlier account already failed.
"""

from __future__ import annotations

from xauusd_bot.domain.models import (
    ExecutionResult,
    OpenTradeQuery,
    PositionCheckResult,
    SymbolSpec,
    TradePlan,
)
from xauusd_bot.logging_setup.logger import get_logger
from xauusd_bot.mt5_adapter.client import Mt5AccountClient
from xauusd_bot.mt5_adapter.errors import Mt5ConnectionError, Mt5OrderError

logger = get_logger(__name__)


class MultiAccountExecutor:
    """Executes a TradePlan for each account, across all 4 MT5 terminals."""

    def __init__(self, clients: list[Mt5AccountClient]) -> None:
        self._clients = clients
        self._clients_by_account_id: dict[int, Mt5AccountClient] = {
            client.account_id: client for client in clients
        }

    def execute_all(self, plans: list[TradePlan]) -> list[ExecutionResult]:
        """Send one order per plan across all managed accounts.

        Attempts every plan in order, connecting/disconnecting each
        account's terminal in turn, even if an earlier plan already failed.
        If any plan fails, every account that DID open a position is rolled
        back (closed); rollback is best-effort and never allowed to raise —
        failures are logged at CRITICAL and reflected in the returned
        ExecutionResult instead (success=False with a manual-intervention
        error_message), so a rollback problem can never mask or crash past
        the original failure.

        Note: unlike the original stub docstring, per-account
        Mt5ConnectionError/Mt5OrderError are always caught here and reported
        via ExecutionResult rather than raised, implementing the confirmed
        partial-failure rollback design.

        Raises:
            ValueError: if ``plans`` references an unknown account_id, or
                contains duplicate account_ids.
        """
        self._validate_plans(plans)

        results = [
            self._attempt_open(self._clients_by_account_id[plan.account_id], plan)
            for plan in plans
        ]

        if all(result.success for result in results):
            logger.info(
                "All %d account(s) executed successfully; no rollback needed", len(results)
            )
            return results

        succeeded = sum(1 for result in results if result.success)
        logger.error(
            "Partial execution failure across accounts (%d/%d succeeded); "
            "rolling back succeeded accounts",
            succeeded,
            len(results),
        )
        return self._rollback_succeeded(plans, results)

    def _validate_plans(self, plans: list[TradePlan]) -> None:
        seen: set[int] = set()
        for plan in plans:
            if plan.account_id in seen:
                raise ValueError(f"Duplicate account_id in plans: {plan.account_id}")
            seen.add(plan.account_id)
            if plan.account_id not in self._clients_by_account_id:
                raise ValueError(f"No Mt5AccountClient configured for account_id={plan.account_id}")

    def _attempt_open(self, client: Mt5AccountClient, plan: TradePlan) -> ExecutionResult:
        """Connect, send one order, and disconnect — never raises."""
        try:
            client.connect()
        except Mt5ConnectionError as exc:
            logger.error("Connect failed for account_id=%s: %s", plan.account_id, exc)
            return ExecutionResult(account_id=plan.account_id, success=False, error_message=str(exc))

        try:
            return client.send_market_order(plan)
        except (Mt5OrderError, Mt5ConnectionError) as exc:
            logger.error("Order failed for account_id=%s: %s", plan.account_id, exc)
            return ExecutionResult(account_id=plan.account_id, success=False, error_message=str(exc))
        finally:
            self._safe_disconnect(client, plan.account_id)

    def _rollback_succeeded(
        self, plans: list[TradePlan], results: list[ExecutionResult]
    ) -> list[ExecutionResult]:
        plans_by_account_id = {plan.account_id: plan for plan in plans}
        final: list[ExecutionResult] = []
        for result in results:
            if not result.success:
                final.append(result)
                continue
            plan = plans_by_account_id[result.account_id]
            client = self._clients_by_account_id[result.account_id]
            final.append(self._rollback_one(client, plan, result))
        return final

    def _rollback_one(
        self, client: Mt5AccountClient, plan: TradePlan, opened: ExecutionResult
    ) -> ExecutionResult:
        """Close a previously-opened position; never raises."""
        ticket = opened.order_ticket
        try:
            client.connect()
            client.close_position(
                account_id=plan.account_id,
                symbol=plan.symbol,
                side=plan.side,
                volume=plan.lot_size,
                position_ticket=ticket,  # type: ignore[arg-type]
            )
        except (Mt5ConnectionError, Mt5OrderError) as exc:
            logger.critical(
                "ROLLBACK FAILED for account_id=%s ticket=%s: %s - position may still be "
                "OPEN, MANUAL INTERVENTION REQUIRED",
                plan.account_id,
                ticket,
                exc,
            )
            result = ExecutionResult(
                account_id=plan.account_id,
                success=False,
                order_ticket=ticket,
                entry_price=opened.entry_price,
                error_message=(
                    f"Order originally succeeded (ticket={ticket}) but rollback failed: "
                    f"{exc}. Position may still be OPEN - MANUAL INTERVENTION REQUIRED."
                ),
            )
        else:
            logger.warning(
                "Rolled back position for account_id=%s ticket=%s after a sibling failure",
                plan.account_id,
                ticket,
            )
            result = ExecutionResult(
                account_id=plan.account_id,
                success=False,
                order_ticket=ticket,
                entry_price=opened.entry_price,
                error_message="Rolled back: closed after a sibling account's order failed",
                rolled_back=True,
            )
        finally:
            self._safe_disconnect(client, plan.account_id)
        return result

    @staticmethod
    def _safe_disconnect(client: Mt5AccountClient, account_id: int) -> None:
        try:
            client.disconnect()
        except Exception:
            logger.exception("Error disconnecting account_id=%s (ignored)", account_id)

    def get_symbol_specs(self, symbol: str) -> dict[int, SymbolSpec]:
        """Fetch each managed account's own SymbolSpec for ``symbol``.

        Required because RiskEngine needs each account's own
        tick_size/tick_value/volume_step (can differ across brokers), not
        just account 1's. Unlike check_trade_statuses, this does NOT
        catch-and-continue per account: a trade plan is invalid without
        every account's spec, so any failure aborts the whole batch — the
        caller should catch Mt5ConnectionError and skip this opportunity
        for the cycle.

        Raises:
            Mt5ConnectionError: if any account's connect()/get_symbol_spec()
                fails.
        """
        specs: dict[int, SymbolSpec] = {}
        for client in self._clients:
            client.connect()
            try:
                specs[client.account_id] = client.get_symbol_spec(symbol)
            finally:
                self._safe_disconnect(client, client.account_id)
        return specs

    def check_trade_statuses(
        self, open_trades: list[OpenTradeQuery]
    ) -> dict[int, PositionCheckResult]:
        """Check MT5 position status for a batch of OPEN trade rows.

        Mirrors _attempt_open's per-account connect/act/disconnect pattern.
        A single account's connectivity failure is logged and reported as
        still_open=True (unknown/retry) rather than raised, so one flaky
        account never blocks reconciling the other trades.
        """
        results: dict[int, PositionCheckResult] = {}
        for query in open_trades:
            client = self._clients_by_account_id.get(query.account_id)
            if client is None:
                logger.error(
                    "No Mt5AccountClient for account_id=%s while checking trade_id=%s; unresolved",
                    query.account_id,
                    query.trade_id,
                )
                results[query.trade_id] = PositionCheckResult(
                    ticket=query.mt5_order_ticket, still_open=True
                )
                continue
            try:
                client.connect()
            except Mt5ConnectionError as exc:
                logger.error(
                    "Connect failed checking trade_id=%s (account_id=%s): %s",
                    query.trade_id,
                    query.account_id,
                    exc,
                )
                results[query.trade_id] = PositionCheckResult(
                    ticket=query.mt5_order_ticket, still_open=True
                )
                continue
            try:
                results[query.trade_id] = client.get_position_status(
                    ticket=query.mt5_order_ticket,
                    symbol=query.symbol,
                    stop_loss=query.stop_loss,
                    take_profit=query.take_profit,
                )
            except Mt5ConnectionError as exc:
                logger.error(
                    "Status check failed trade_id=%s (account_id=%s): %s",
                    query.trade_id,
                    query.account_id,
                    exc,
                )
                results[query.trade_id] = PositionCheckResult(
                    ticket=query.mt5_order_ticket, still_open=True
                )
            finally:
                self._safe_disconnect(client, query.account_id)
        return results
