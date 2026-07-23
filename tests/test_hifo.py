"""Gated HIFO selection, isolation, exact ranking, and unknown-basis checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from engine.accounting import (
    FifoLedger,
    HifoLedger,
    TaxLotLedger,
    UnrankableHifoBasisError,
)
from engine.country_US import UsTaxReportHandler
from engine.models import (
    Direction,
    DisposalRow,
    FeeEvidence,
    LotConsumption,
    NormalizedEvent,
    SourceReference,
    TokenLeg,
    TransactionStatus,
)
from engine.policy.config import US_FIFO_POLICY

WALLET = "synthetic_wallet_hifo"
MINT = "synthetic_mint_hifo"

def _event(
    name: str,
    *,
    direction: Direction,
    quantity: int,
    timestamp: datetime,
    cash: Decimal | None,
) -> NormalizedEvent:
    source = SourceReference(
        source_id=f"src_{name}",
        fixture_set_id="synthetic_hifo_comparison",
        wallet_id=WALLET,
        fixture_record_id=name,
        signature=f"synthetic_signature_{name}",
    )
    return NormalizedEvent(
        event_id=f"evt_{name}",
        source_refs=(source,),
        timestamp_utc=timestamp,
        slot=1,
        instruction_index=0,
        wallet_id=WALLET,
        status=TransactionStatus.SUCCEEDED,
        program="synthetic_hifo_test",
        instruction_type="buy" if direction is Direction.INBOUND else "sell",
        token_legs=(
            TokenLeg(
                component_id=f"cmp_{name}",
                mint=MINT,
                symbol="HIFO",
                decimals=6,
                direction=direction,
                quantity_base_units=quantity,
            ),
        ),
        fee=FeeEvidence(WALLET, 0, False),
        cash_value_usd=cash,
        counterparty_wallet_id=None,
    )

def _project(method: TaxLotLedger) -> tuple[TaxLotLedger, LotConsumption, DisposalRow]:
    old_time = datetime(2025, 1, 1, tzinfo=UTC)
    method.acquire(
        _event(
            "buy-old-low",
            direction=Direction.INBOUND,
            quantity=2_000_000,
            timestamp=old_time,
            cash=Decimal("4"),
        )
    )
    method.acquire(
        _event(
            "buy-new-high",
            direction=Direction.INBOUND,
            quantity=2_000_000,
            timestamp=old_time + timedelta(days=1),
            cash=Decimal("10"),
        )
    )
    sale = _event(
        "sale-comparison",
        direction=Direction.OUTBOUND,
        quantity=2_000_000,
        timestamp=old_time + timedelta(days=40),
        cash=Decimal("14"),
    )
    consumptions = method.consume(sale)
    rows = UsTaxReportHandler(policy=US_FIFO_POLICY).prepare_disposal_rows(
        event=sale,
        consumptions=consumptions,
        ledger=method,
    )
    return method, consumptions[0], rows[0]

def test_hifo_changes_only_selected_lot_and_derived_basis() -> None:
    fifo, fifo_consumption, fifo_row = _project(FifoLedger())
    hifo, hifo_consumption, hifo_row = _project(HifoLedger())

    # HIFO is deliberately a selection specialization. If it overrides mutation methods, it can
    # drift from FIFO's splitting, movement, and conservation invariants.
    assert not {
        "acquire",
        "acquire_unknown",
        "consume",
        "convert",
        "move",
        "suspend",
    } & HifoLedger.__dict__.keys()
    assert fifo.method == "FIFO"
    assert hifo.method == "HIFO"
    # Both projections dispose the same quantity for the same proceeds. Only the selected lot and
    # its derived basis and gain are expected to differ.
    assert fifo_consumption.lot_id != hifo_consumption.lot_id
    assert fifo_consumption.consumed_basis.require_total_usd() == Decimal("4")
    assert hifo_consumption.consumed_basis.require_total_usd() == Decimal("10")
    assert fifo_row.quantity_base_units == hifo_row.quantity_base_units == 2_000_000
    assert fifo_row.proceeds_usd == hifo_row.proceeds_usd == Decimal("14")
    assert fifo_row.gain_loss_usd == Decimal("10")
    assert hifo_row.gain_loss_usd == Decimal("4")
    assert fifo_row.event_id == hifo_row.event_id == "evt_sale-comparison"
    assert fifo.all_movements() == hifo.all_movements() == ()

def test_hifo_uses_acquisition_then_lot_id_for_exact_unit_basis_ties() -> None:
    ledger = HifoLedger()
    acquired_at = datetime(2025, 1, 1, tzinfo=UTC)
    first = ledger.acquire(
        _event(
            "tie-alpha",
            direction=Direction.INBOUND,
            quantity=2_000_000,
            timestamp=acquired_at,
            cash=Decimal("8"),
        )
    )
    second = ledger.acquire(
        _event(
            "tie-zulu",
            direction=Direction.INBOUND,
            quantity=1_000_000,
            timestamp=acquired_at,
            cash=Decimal("4"),
        )
    )
    sale = _event(
        "tie-sale",
        direction=Direction.OUTBOUND,
        quantity=1_000_000,
        timestamp=acquired_at + timedelta(days=1),
        cash=Decimal("5"),
    )

    consumption = ledger.consume(sale)[0]

    # Equal unit basis and acquisition time need a content-stable final tie-breaker; otherwise
    # fixture or database iteration order could change the selected lot.
    assert consumption.lot_id == min(first.lot_id, second.lot_id)
    assert consumption.consumed_basis.require_total_usd() == Decimal("4")

def test_hifo_unknown_basis_fails_before_inventory_mutation() -> None:
    ledger = HifoLedger()
    acquired_at = datetime(2025, 1, 1, tzinfo=UTC)
    ledger.acquire(
        _event(
            "known",
            direction=Direction.INBOUND,
            quantity=1_000_000,
            timestamp=acquired_at,
            cash=Decimal("4"),
        )
    )
    unknown = ledger.acquire_unknown(
        _event(
            "unknown",
            direction=Direction.INBOUND,
            quantity=1_000_000,
            timestamp=acquired_at + timedelta(days=1),
            cash=None,
        ),
        exception_id="exc_hifo_unknown_basis",
    )
    before = ledger.all_lots()

    # Assigning unknown basis a zero or infinite rank would invent selection evidence. The failure
    # must be atomic, which the unchanged before-state and empty consumptions prove below.
    with pytest.raises(UnrankableHifoBasisError, match=unknown.lot_id):
        ledger.consume(
            _event(
                "blocked-sale",
                direction=Direction.OUTBOUND,
                quantity=1_000_000,
                timestamp=acquired_at + timedelta(days=2),
                cash=Decimal("6"),
            )
        )

    assert ledger.all_lots() == before
    assert ledger.all_consumptions() == ()
