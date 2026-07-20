"""Resolve only explicit exact-timestamp price evidence."""

from __future__ import annotations

from datetime import datetime

from engine.models import PricePoint, PriceResolution, PriceResolutionState
from engine.policy.config import AssessmentPolicy, PriceRule


def resolve_price(
    prices: tuple[PricePoint, ...],
    *,
    mint: str,
    timestamp_utc: datetime,
    policy: AssessmentPolicy,
) -> PriceResolution:
    """Return found, missing, or conflict without estimating a price."""
    if policy.price_rule is not PriceRule.EXACT_TIMESTAMP:
        raise ValueError(f"Unsupported price rule: {policy.price_rule.value}.")
    matches = tuple(
        sorted(
            (
                point
                for point in prices
                if point.mint == mint
                and point.timestamp_utc == timestamp_utc
                and point.valuation_currency == policy.valuation_currency.value
            ),
            key=lambda point: point.price_id,
        )
    )
    if not matches:
        state = PriceResolutionState.MISSING
    elif len(matches) == 1:
        state = PriceResolutionState.FOUND
    else:
        state = PriceResolutionState.CONFLICT
    return PriceResolution(
        state=state,
        mint=mint,
        timestamp_utc=timestamp_utc,
        valuation_currency=policy.valuation_currency.value,
        matches=matches,
    )
