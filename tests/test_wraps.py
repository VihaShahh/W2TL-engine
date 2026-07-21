"""Supported continuity and ambiguous wrap policy checks."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from engine.accounting import FifoLedger, account_wrap
from engine.core.exceptions import ExceptionCollector
from engine.models import (
    Direction,
    FeeEvidence,
    InventoryState,
    NormalizedEvent,
    SourceReference,
    TokenLeg,
    TransactionStatus,
)

WALLET = "synthetic_wallet_wrap_test"
SKITO = "synthetic_mint_skito"
WRAPPED_SKITO = "synthetic_mint_wrapped_skito"

def _event(
    event_id: str,
    *,
    instruction_type: str,
    program: str,
    legs: tuple[TokenLeg, ...],
    timestamp: datetime,
    cash_value_usd: Decimal | None = None,
) -> NormalizedEvent:
    source = SourceReference(
        source_id=f"src_{event_id}",
        fixture_set_id="focused_wrap_scenarios",
        wallet_id=WALLET,
        fixture_record_id=f"record_{event_id}",
        signature=f"synthetic_signature_{event_id}",
    )
    return NormalizedEvent(
        event_id=event_id,
        source_refs=(source,),
        timestamp_utc=timestamp,
        slot=1,
        instruction_index=0,
        wallet_id=WALLET,
        status=TransactionStatus.SUCCEEDED,
        program=program,
        instruction_type=instruction_type,
        token_legs=legs,
        fee=FeeEvidence(payer_wallet_id=WALLET, fee_base_units=0, charged=False),
        cash_value_usd=cash_value_usd,
        counterparty_wallet_id=None,
    )

def _leg(event_id: str, mint: str, direction: Direction, quantity: int) -> TokenLeg:
    return TokenLeg(
        component_id=f"leg_{event_id}_{direction.value}",
        mint=mint,
        symbol="wSKITO" if mint == WRAPPED_SKITO else "SKITO",
        decimals=6,
        direction=direction,
        quantity_base_units=quantity,
    )

def _ledger_with_skito() -> tuple[FifoLedger, datetime]:
    acquired_at = datetime(2024, 1, 1, tzinfo=UTC)
    ledger = FifoLedger()
    ledger.acquire(
        _event(
            "evt_wrap_source_buy",
            instruction_type="buy",
            program="synthetic_exchange",
            legs=(_leg("buy", SKITO, Direction.INBOUND, 3_000_000),),
            timestamp=acquired_at,
            cash_value_usd=Decimal("12"),
        )
    )
    return ledger, acquired_at

def test_supported_one_to_one_wrap_carries_basis_date_and_lineage() -> None:
    ledger, acquired_at = _ledger_with_skito()
    collector = ExceptionCollector()
    event = _event(
        "evt_supported_wrap",
        instruction_type="wrap",
        program="synthetic_wrap_program",
        legs=(
            _leg("wrap", SKITO, Direction.OUTBOUND, 2_000_000),
            _leg("wrap", WRAPPED_SKITO, Direction.INBOUND, 2_000_000),
        ),
        timestamp=datetime(2025, 5, 1, tzinfo=UTC),
    )

    result = account_wrap(event=event, ledger=ledger, collector=collector)
    destination = result.destination_lots[0]

    assert result.is_continuous
    assert collector.items() == ()
    assert destination.mint == WRAPPED_SKITO
    assert destination.acquisition.require_timestamp_utc() == acquired_at
    assert destination.starting_basis.require_total_usd() == Decimal("8")
    assert destination.parent_lot_id == result.movements[0].source_lot_id

def test_ambiguous_wrap_preserves_observed_units_without_basis_continuity() -> None:
    ledger, _ = _ledger_with_skito()
    collector = ExceptionCollector()
    event = _event(
        "evt_ambiguous_wrap",
        instruction_type="wrap",
        program="synthetic_wrap_program",
        legs=(
            _leg("ambiguous", SKITO, Direction.OUTBOUND, 2_000_000),
            _leg("ambiguous", WRAPPED_SKITO, Direction.INBOUND, 1_500_000),
        ),
        timestamp=datetime(2025, 5, 2, tzinfo=UTC),
    )

    result = account_wrap(event=event, ledger=ledger, collector=collector)
    destination = result.destination_lots[0]

    assert not result.is_continuous
    assert collector.items()[0].code == "ambiguous_wrap_treatment"
    assert destination.state is InventoryState.QUARANTINED
    assert destination.remaining_quantity_base_units == 1_500_000
    assert not destination.starting_basis.is_known
    assert ledger.open_lots()[0].remaining_quantity_base_units == 1_000_000
    assert any(lot.state is InventoryState.SUSPENSE for lot in ledger.all_lots())
