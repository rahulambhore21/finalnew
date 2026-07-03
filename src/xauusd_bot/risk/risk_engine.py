"""Calculates lot size, stop loss, and take profit from dollar-based risk targets.

Otherwise a pure calculator (no MT5/network I/O, fully unit-testable) except
for logging: every account's symbol spec and derived money-per-price-unit is
logged, and each spec's tick_value is cross-checked against
``contract_size * tick_size`` before being trusted, per CLAUDE.md's "never
trust MT5 responses without validation" rule — a mismatch there (e.g. a
stale or wrong-symbol MT5 response) silently produces an SL/TP distance that
is off by whatever factor the mismatch introduces, even though the
risk_usd/reward_usd arithmetic downstream looks perfectly self-consistent.
Never calculates TP/SL using fixed price distances; always derives them from
each account's MT5 symbol specification (tick size/value, contract size,
volume step) per CLAUDE.md.

All arithmetic is performed with :class:`decimal.Decimal` (converted from the
input floats via their ``str()`` representation) rather than raw floats, so
that lot-size/tick-size multiple checks and price rounding are exact and
never suffer from binary floating-point artifacts (e.g. ``0.05 / 0.01``
evaluating to ``4.999999999999999`` instead of ``5``). Results are converted
back to ``float`` only at the boundary, when populating the pydantic domain
models.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from xauusd_bot.config.settings import AccountCredentials
from xauusd_bot.domain.models import RiskParameters, SymbolSpec, TradePlan
from xauusd_bot.logging_setup.logger import get_logger
from xauusd_bot.risk.errors import RiskCalculationError

logger = get_logger(__name__)

#: For an instrument settled in the account's own currency, MT5 defines
#: tick_value as contract_size * tick_size (the P/L of a one-tick move for
#: one lot). If the account's profit currency differs from the deposit
#: currency, a currency-conversion factor multiplies that — but for XAUUSD
#: (profit currency USD) against any realistic deposit currency, that factor
#: stays within roughly +/-50%. A deviation past this ratio means
#: tick_value/tick_size/contract_size are internally inconsistent (e.g. a
#: stale or wrong-symbol MT5 response), which silently scales every SL/TP
#: distance computed from them even though the risk_usd/reward_usd
#: arithmetic downstream looks perfectly self-consistent.
MAX_TICK_VALUE_DEVIATION_RATIO = 0.5


def _to_decimal(value: float) -> Decimal:
    """Convert a float to Decimal via its ``str()`` to avoid binary float noise."""
    return Decimal(str(value))


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round ``value`` down to the nearest multiple of the positive ``step``."""
    if step <= 0:
        raise RiskCalculationError(f"step must be positive, got {step}")
    quotient = (value / step).to_integral_value(rounding=ROUND_FLOOR)
    return quotient * step


def _ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round ``value`` up to the nearest multiple of the positive ``step``."""
    if step <= 0:
        raise RiskCalculationError(f"step must be positive, got {step}")
    quotient = (value / step).to_integral_value(rounding=ROUND_CEILING)
    return quotient * step


def _is_multiple_of(value: Decimal, step: Decimal) -> bool:
    """True if ``value`` is an exact (integer) multiple of the positive ``step``."""
    quotient = value / step
    return quotient == quotient.to_integral_value()


def _determine_shared_lot_size(
    risk_params: RiskParameters, specs: tuple[SymbolSpec, ...]
) -> Decimal:
    """Pick one lot size, starting from ``preferred_lot_size``, valid for every account.

    Rounds ``preferred_lot_size`` down to the coarsest ``volume_step`` among
    all given specs, clamps it into
    ``[max(lot_min, max volume_min), min(lot_max, min volume_max)]``, then
    verifies the result is within every account's own bounds and an exact
    multiple of every account's own ``volume_step``.

    Raises:
        RiskCalculationError: if no single lot size satisfies the configured
            ``[lot_min, lot_max]`` bounds and every account's
            volume_min/volume_max/volume_step simultaneously.
    """
    preferred = _to_decimal(risk_params.preferred_lot_size)
    lot_min = _to_decimal(risk_params.lot_min)
    lot_max = _to_decimal(risk_params.lot_max)

    steps = [_to_decimal(s.volume_step) for s in specs]
    vol_mins = [_to_decimal(s.volume_min) for s in specs]
    vol_maxs = [_to_decimal(s.volume_max) for s in specs]

    coarsest_step = max(steps)
    lot = _floor_to_step(preferred, coarsest_step)

    if lot < lot_min:
        raise RiskCalculationError(
            f"Preferred/AI recommended lot size {preferred} (rounded to {lot}) is below "
            f"the configured minimum lot size {lot_min}. The stop loss is too wide."
        )

    lower_bound = max(lot_min, max(vol_mins))
    upper_bound = min(lot_max, min(vol_maxs))
    lot = max(lower_bound, min(upper_bound, lot))

    if lot < lot_min or lot > lot_max:
        raise RiskCalculationError(
            f"No valid shared lot size: {lot} falls outside configured bounds "
            f"[{lot_min}, {lot_max}] after rounding/clamping "
            f"preferred_lot_size={preferred}"
        )

    for spec, step, vmin, vmax in zip(specs, steps, vol_mins, vol_maxs):
        if lot < vmin or lot > vmax:
            raise RiskCalculationError(
                f"Lot size {lot} is invalid for account symbol {spec.symbol!r}: "
                f"outside that account's [volume_min={vmin}, volume_max={vmax}]"
            )
        if not _is_multiple_of(lot, step):
            raise RiskCalculationError(
                f"Lot size {lot} is not a multiple of volume_step={step} "
                f"for account symbol {spec.symbol!r}"
            )

    return lot


def _validate_tick_value_consistency(account_id: int, spec: SymbolSpec) -> None:
    """Raise if ``tick_value`` isn't within a plausible multiple of ``contract_size * tick_size``.

    This is the invariant MT5 itself guarantees (up to a currency-conversion
    factor when the account's deposit currency isn't the instrument's profit
    currency): ``tick_value`` is defined as the P/L of a one-tick move for one
    lot, i.e. ``contract_size * tick_size`` in the profit currency. Checking
    it here catches a wrong/stale ``tick_value`` (or ``tick_size`` /
    ``contract_size``) from ``symbol_info()`` before it silently scales every
    SL/TP distance computed downstream — see :data:`MAX_TICK_VALUE_DEVIATION_RATIO`.

    Raises:
        RiskCalculationError: if the deviation ratio exceeds
            ``MAX_TICK_VALUE_DEVIATION_RATIO``.
    """
    tick_size = _to_decimal(spec.tick_size)
    tick_value = _to_decimal(spec.tick_value)
    contract_size = _to_decimal(spec.contract_size)
    if contract_size <= 0:
        raise RiskCalculationError(
            f"Symbol spec for {spec.symbol!r} has non-positive contract_size={contract_size}"
        )

    expected_tick_value = contract_size * tick_size
    deviation_ratio = abs(tick_value - expected_tick_value) / expected_tick_value
    if deviation_ratio > Decimal(str(MAX_TICK_VALUE_DEVIATION_RATIO)):
        raise RiskCalculationError(
            f"tick_value sanity check failed for account_id={account_id} "
            f"symbol={spec.symbol!r}: tick_value={tick_value} but "
            f"contract_size({contract_size}) * tick_size({tick_size}) = "
            f"{expected_tick_value}, deviating by {float(deviation_ratio):.1%} — "
            f"exceeds {MAX_TICK_VALUE_DEVIATION_RATIO:.0%} tolerance. This symbol spec "
            f"looks internally inconsistent (stale/wrong-symbol MT5 response?); "
            f"refusing to size a trade from it"
        )


def _compute_account_prices(
    account_id: int,
    lot_size: Decimal,
    spec: SymbolSpec,
    entry_price: Decimal,
    side: str,
    risk_params: RiskParameters,
) -> tuple[Decimal, Decimal]:
    """Compute one account's (stop_loss, take_profit) prices for the shared lot_size.

    Derives the SL/TP price distance purely from the dollar risk/reward
    targets and this account's own tick_size/tick_value (never a fixed price
    distance). The SL distance is floored and the TP distance is ceiled to
    the nearest tick_size multiple, so realized loss never exceeds
    ``risk_usd`` and realized profit is never less than ``reward_usd``. Logs
    the full spec/distance/dollar-amount breakdown for this account, and
    validates ``tick_value`` against ``contract_size``/``tick_size`` before
    trusting it (see :func:`_validate_tick_value_consistency`).

    Raises:
        RiskCalculationError: if the symbol spec is degenerate (non-positive
            tick_size/tick_value/contract_size), if ``tick_value`` is
            inconsistent with ``contract_size * tick_size`` by more than
            ``MAX_TICK_VALUE_DEVIATION_RATIO``, if the computed SL or TP
            distance rounds down/up to zero, or if the resulting price is
            non-positive or equal to the entry price.
    """
    tick_size = _to_decimal(spec.tick_size)
    tick_value = _to_decimal(spec.tick_value)
    if tick_size <= 0 or tick_value <= 0:
        raise RiskCalculationError(
            f"Symbol spec for {spec.symbol!r} has non-positive tick_size/tick_value: "
            f"tick_size={tick_size}, tick_value={tick_value}"
        )
    _validate_tick_value_consistency(account_id, spec)

    money_per_price_unit = (tick_value / tick_size) * lot_size
    if money_per_price_unit <= 0:
        raise RiskCalculationError(
            f"Non-positive money_per_price_unit for symbol {spec.symbol!r}"
        )

    logger.info(
        "Risk calc account_id=%s symbol=%s lot_size=%s tick_size=%s tick_value=%s "
        "contract_size=%s money_per_price_unit=%s risk_usd=%s reward_usd=%s",
        account_id,
        spec.symbol,
        lot_size,
        tick_size,
        tick_value,
        spec.contract_size,
        money_per_price_unit,
        risk_params.risk_usd,
        risk_params.reward_usd,
    )

    raw_sl_distance = _to_decimal(risk_params.risk_usd) / money_per_price_unit
    raw_tp_distance = _to_decimal(risk_params.reward_usd) / money_per_price_unit

    sl_distance = _floor_to_step(raw_sl_distance, tick_size)
    tp_distance = _ceil_to_step(raw_tp_distance, tick_size)

    if sl_distance <= 0 or tp_distance <= 0:
        raise RiskCalculationError(
            f"Computed a zero-distance SL/TP for symbol {spec.symbol!r}: "
            f"risk_usd/reward_usd too small (or lot_size/tick_value too large) "
            f"relative to tick_size={tick_size}"
        )

    actual_risk_usd = sl_distance * money_per_price_unit
    actual_reward_usd = tp_distance * money_per_price_unit

    if side == "BUY":
        stop_loss = entry_price - sl_distance
        take_profit = entry_price + tp_distance
    else:
        stop_loss = entry_price + sl_distance
        take_profit = entry_price - tp_distance

    quantum = Decimal(f"1e-{spec.digits}")
    stop_loss = stop_loss.quantize(quantum, rounding=ROUND_HALF_UP)
    take_profit = take_profit.quantize(quantum, rounding=ROUND_HALF_UP)

    if stop_loss <= 0 or take_profit <= 0:
        raise RiskCalculationError(
            f"Computed non-positive SL/TP price for symbol {spec.symbol!r}: "
            f"stop_loss={stop_loss}, take_profit={take_profit}"
        )
    if stop_loss == entry_price or take_profit == entry_price:
        raise RiskCalculationError(
            f"Computed SL/TP equal to entry_price for symbol {spec.symbol!r}: "
            f"entry_price={entry_price}"
        )

    logger.info(
        "Risk calc account_id=%s symbol=%s side=%s entry_price=%s sl_distance=%s "
        "tp_distance=%s stop_loss=%s take_profit=%s actual_risk_usd=%s actual_reward_usd=%s",
        account_id,
        spec.symbol,
        side,
        entry_price,
        sl_distance,
        tp_distance,
        stop_loss,
        take_profit,
        actual_risk_usd,
        actual_reward_usd,
    )

    return stop_loss, take_profit


class RiskEngine:
    """Computes identical lot size, per-account SL/TP prices from dollar risk/reward."""

    def __init__(self, risk_params: RiskParameters) -> None:
        self._risk_params = risk_params

    def compute_trade_plans(
        self,
        accounts: tuple[AccountCredentials, ...],
        symbol_specs: dict[int, SymbolSpec],
        entry_price: float,
        preferred_lot_size: float | None = None,
    ) -> list[TradePlan]:
        """Compute one TradePlan per account, all sharing the same lot size.

        Starts from ``preferred_lot_size`` (or ``risk_params.preferred_lot_size``
        if not provided) and adjusts it to be
        valid for every account in ``accounts`` (rounding down to the
        coarsest volume_step among them, then clamping into each account's
        volume_min/volume_max and the configured lot_min/lot_max). Then, for
        each account, derives its own SL/TP price distance from
        ``risk_params.risk_usd``/``reward_usd`` and that account's own
        tick_size/tick_value (never a fixed price distance), applied around
        the shared ``entry_price`` in the direction of that account's side.

        Raises:
            RiskCalculationError: if no lot size within bounds satisfies
                every account's volume_min/volume_max/volume_step, if a
                symbol_specs entry is missing for one of the accounts, if any
                account's SL/TP calculation is degenerate (zero distance,
                non-positive price, or a price equal to entry_price), or if
                an account's tick_value looks inconsistent with its
                contract_size/tick_size (see
                ``MAX_TICK_VALUE_DEVIATION_RATIO``).
        """
        risk_params = self._risk_params
        if preferred_lot_size is not None:
            risk_params = RiskParameters(
                risk_usd=self._risk_params.risk_usd,
                reward_usd=self._risk_params.reward_usd,
                lot_min=self._risk_params.lot_min,
                lot_max=self._risk_params.lot_max,
                preferred_lot_size=preferred_lot_size,
            )

        specs: list[SymbolSpec] = []
        for account in accounts:
            spec = symbol_specs.get(account.account_id)
            if spec is None:
                raise RiskCalculationError(
                    f"No SymbolSpec provided for account_id={account.account_id}"
                )
            specs.append(spec)

        lot_size = _determine_shared_lot_size(risk_params, tuple(specs))
        entry_price_decimal = _to_decimal(entry_price)
        lot_size_float = float(lot_size)

        plans: list[TradePlan] = []
        for account, spec in zip(accounts, specs):
            stop_loss, take_profit = _compute_account_prices(
                account.account_id, lot_size, spec, entry_price_decimal, account.side, risk_params
            )
            plans.append(
                TradePlan(
                    account_id=account.account_id,
                    side=account.side,
                    symbol=spec.symbol,
                    lot_size=lot_size_float,
                    stop_loss=float(stop_loss),
                    take_profit=float(take_profit),
                    estimated_entry_price=entry_price,
                )
            )
        return plans
