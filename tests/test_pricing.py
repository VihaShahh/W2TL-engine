"""Exact-timestamp price resolution without valuation fallbacks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from engine.evidence.loader import load_fixture
from engine.models import (
    PricePoint,
    PriceResolution,
    PriceResolutionState,
)
from engine.policy.config import US_FIFO_POLICY
from engine.policy.pricing import resolve_price

MINT = "synthetic_mint_skito"
TIMESTAMP = datetime(2025, 8, 2, tzinfo=UTC)

def _price(
    price_id: str,
    value: str,
    *,
    timestamp: datetime = TIMESTAMP,
    currency: str = "USD",
) -> PricePoint:
    return PricePoint(
        price_id=price_id,
        mint=MINT,
        timestamp_utc=timestamp,
        valuation_currency=currency,
        price_usd=Decimal(value),
    )

def test_exact_price_returns_decimal_and_source_identity() -> None:
    fixture = load_fixture(Path("fixtures/complete"), policy=US_FIFO_POLICY)

    resolution = resolve_price(
        fixture.prices,
        mint=MINT,
        timestamp_utc=TIMESTAMP,
        policy=US_FIFO_POLICY,
    )

    assert resolution.state is PriceResolutionState.FOUND
    assert resolution.require_price_usd() == Decimal("4.00")
    assert resolution.require_source_id() == "price_complete-swap-valued-01_skito"

def test_missing_price_never_uses_nearest_prior_currency_or_zero_fallback() -> None:
    prices = (
        _price("prior", "2.00", timestamp=TIMESTAMP - timedelta(seconds=1)),
        _price("future", "4.00", timestamp=TIMESTAMP + timedelta(seconds=1)),
        _price("other_currency", "3.00", currency="EUR"),
    )

    resolution = resolve_price(
        prices,
        mint=MINT,
        timestamp_utc=TIMESTAMP,
        policy=US_FIFO_POLICY,
    )

    assert resolution.state is PriceResolutionState.MISSING
    assert resolution.matches == ()
    with pytest.raises(ValueError, match="no dollar value"):
        resolution.require_price_usd()

def test_multiple_exact_sources_return_conflict_without_choosing_a_value() -> None:
    resolution = resolve_price(
        (_price("source_b", "2.10"), _price("source_a", "2.00")),
        mint=MINT,
        timestamp_utc=TIMESTAMP,
        policy=US_FIFO_POLICY,
    )

    assert resolution.state is PriceResolutionState.CONFLICT
    assert resolution.source_ids == ("source_a", "source_b")
    with pytest.raises(ValueError, match="no dollar value"):
        resolution.require_price_usd()
    with pytest.raises(ValueError, match="no single source"):
        resolution.require_source_id()

def test_price_models_reject_zero_and_contradictory_resolution_states() -> None:
    with pytest.raises(ValueError, match="positive Decimal"):
        _price("zero", "0")
    with pytest.raises(ValueError, match="positive Decimal"):
        PricePoint(
            price_id="float",
            mint=MINT,
            timestamp_utc=TIMESTAMP,
            valuation_currency="USD",
            price_usd=2.0,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="requires 1 source"):
        PriceResolution(
            state=PriceResolutionState.FOUND,
            mint=MINT,
            timestamp_utc=TIMESTAMP,
            valuation_currency="USD",
            matches=(),
        )
