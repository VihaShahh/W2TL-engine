"""Explicit specific-identification selection and no-fallback validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from engine.accounting import (
    InvalidSpecificIdentificationError,
    MissingSpecificIdentificationError,
    SpecificIdentificationLedger,
)
from engine.models import (
    Direction,
    FeeEvidence,
    Lot,
    NormalizedEvent,
    SourceReference,
    SpecificIdentificationManifest,
    SpecificLotSelection,
    TokenLeg,
    TransactionStatus,
)

WALLET = "synthetic_wallet_specific"
OTHER_WALLET = "synthetic_wallet_other"
MINT = "synthetic_mint_specific"
OTHER_MINT = "synthetic_mint_other"

def _event(
    name: str,
    *,
    direction: Direction,
    quantity: int,
    timestamp: datetime,
    cash: Decimal | None,
    wallet_id: str = WALLET,
    mint: str = MINT,
) -> NormalizedEvent:
    source = SourceReference(
        source_id=f"src_{name}",
        fixture_set_id="synthetic_specific_identification",
        wallet_id=wallet_id,
        fixture_record_id=name,
        signature=f"synthetic_signature_{name}",
    )
    return NormalizedEvent(
        event_id=f"evt_{name}",
        source_refs=(source,),
        timestamp_utc=timestamp,
        slot=1,
        instruction_index=0,
        wallet_id=wallet_id,
        status=TransactionStatus.SUCCEEDED,
        program="synthetic_specific_test",
        instruction_type="buy" if direction is Direction.INBOUND else "sell",
        token_legs=(
            TokenLeg(
                component_id=f"cmp_{name}",
                mint=mint,
                symbol="SPEC",
                decimals=6,
                direction=direction,
                quantity_base_units=quantity,
            ),
        ),
        fee=FeeEvidence(wallet_id, 0, False),
        cash_value_usd=cash,
        counterparty_wallet_id=None,
    )

def _inventory() -> tuple[SpecificIdentificationLedger, Lot, Lot, datetime]:
    ledger = SpecificIdentificationLedger()
    acquired_at = datetime(2025, 1, 1, tzinfo=UTC)
    first = ledger.acquire(
        _event(
            "buy-first",
            direction=Direction.INBOUND,
            quantity=2_000_000,
            timestamp=acquired_at,
            cash=Decimal("4"),
        )
    )
    second = ledger.acquire(
        _event(
            "buy-second",
            direction=Direction.INBOUND,
            quantity=2_000_000,
            timestamp=acquired_at + timedelta(days=1),
            cash=Decimal("10"),
        )
    )
    return ledger, first, second, acquired_at

def test_valid_manifest_consumes_only_explicit_lot_quantities() -> None:
    ledger, first, second, acquired_at = _inventory()
    sale = _event(
        "selected-sale",
        direction=Direction.OUTBOUND,
        quantity=2_000_000,
        timestamp=acquired_at + timedelta(days=20),
        cash=Decimal("14"),
    )
    manifest = SpecificIdentificationManifest(
        disposal_event_id=sale.event_id,
        wallet_id=WALLET,
        mint=MINT,
        selections=(
            SpecificLotSelection(lot_id=second.lot_id, quantity_base_units=1_000_000),
            SpecificLotSelection(lot_id=first.lot_id, quantity_base_units=1_000_000),
        ),
    )

    consumptions = ledger.consume_manifest(sale, manifest=manifest)

    # Manifest order and quantities are the evidence for selection; the ledger must not reorder
    # them as FIFO would. Shared splitting still leaves exact remainders in both source lots.
    assert ledger.method == "SPECIFIC_IDENTIFICATION"
    assert [item.lot_id for item in consumptions] == [second.lot_id, first.lot_id]
    assert [item.quantity_base_units for item in consumptions] == [1_000_000, 1_000_000]
    assert [item.consumed_basis.require_total_usd() for item in consumptions] == [
        Decimal("5"),
        Decimal("2"),
    ]
    assert ledger.get_lot(first.lot_id).remaining_quantity_base_units == 1_000_000
    assert ledger.get_lot(second.lot_id).remaining_quantity_base_units == 1_000_000

def test_missing_manifest_never_falls_back_to_automatic_ordering() -> None:
    ledger, _, _, acquired_at = _inventory()
    before = ledger.all_lots()
    sale = _event(
        "missing-manifest",
        direction=Direction.OUTBOUND,
        quantity=1_000_000,
        timestamp=acquired_at + timedelta(days=20),
        cash=Decimal("7"),
    )

    # A FIFO fallback would produce a plausible number while falsely claiming specific
    # identification. Missing evidence therefore leaves inventory completely unchanged.
    with pytest.raises(MissingSpecificIdentificationError, match="explicit"):
        ledger.consume(sale)

    assert ledger.all_lots() == before
    assert ledger.all_consumptions() == ()

def test_manifest_rejects_wrong_wallet_lot_without_mutation() -> None:
    ledger, first, _, acquired_at = _inventory()
    other = ledger.acquire(
        _event(
            "other-wallet-buy",
            direction=Direction.INBOUND,
            quantity=1_000_000,
            timestamp=acquired_at,
            cash=Decimal("3"),
            wallet_id=OTHER_WALLET,
        )
    )
    sale = _event(
        "wrong-wallet-sale",
        direction=Direction.OUTBOUND,
        quantity=2_000_000,
        timestamp=acquired_at + timedelta(days=20),
        cash=Decimal("12"),
    )
    manifest = SpecificIdentificationManifest(
        disposal_event_id=sale.event_id,
        wallet_id=WALLET,
        mint=MINT,
        selections=(
            SpecificLotSelection(first.lot_id, 1_000_000),
            SpecificLotSelection(other.lot_id, 1_000_000),
        ),
    )
    before = ledger.all_lots()

    # Lot IDs are not enough: basis scope is wallet-and-mint. Prevalidating the complete manifest
    # prevents the valid first selection from applying before the invalid second selection fails.
    with pytest.raises(InvalidSpecificIdentificationError, match="another wallet"):
        ledger.consume_manifest(sale, manifest=manifest)

    assert ledger.all_lots() == before
    assert ledger.all_consumptions() == ()

@pytest.mark.parametrize(
    ("case", "error"),
    (
        ("wrong_mint", "another mint"),
        ("missing_lot", "does not exist"),
        ("overdraw", "insufficient remaining quantity"),
        ("quantity_mismatch", "do not equal"),
    ),
)
def test_invalid_manifest_is_rejected_before_mutation(case: str, error: str) -> None:
    ledger, first, _, acquired_at = _inventory()
    other_mint = ledger.acquire(
        _event(
            "other-mint-buy",
            direction=Direction.INBOUND,
            quantity=2_000_000,
            timestamp=acquired_at,
            cash=Decimal("6"),
            mint=OTHER_MINT,
        )
    )
    sale_quantity = 2_500_000 if case == "overdraw" else 2_000_000
    sale = _event(
        f"{case}-sale",
        direction=Direction.OUTBOUND,
        quantity=sale_quantity,
        timestamp=acquired_at + timedelta(days=20),
        cash=Decimal("14"),
    )
    selection = {
        "wrong_mint": SpecificLotSelection(other_mint.lot_id, 2_000_000),
        "missing_lot": SpecificLotSelection("lot_missing", 2_000_000),
        "overdraw": SpecificLotSelection(first.lot_id, 2_500_000),
        "quantity_mismatch": SpecificLotSelection(first.lot_id, 1_000_000),
    }[case]
    manifest = SpecificIdentificationManifest(
        disposal_event_id=sale.event_id,
        wallet_id=WALLET,
        mint=MINT,
        selections=(selection,),
    )
    before = ledger.all_lots()

    # Every manifest defect is checked before mutation, so all error variants share the same
    # all-or-nothing inventory guarantee.
    with pytest.raises(InvalidSpecificIdentificationError, match=error):
        ledger.consume_manifest(sale, manifest=manifest)

    assert ledger.all_lots() == before
    assert ledger.all_consumptions() == ()

def test_manifest_contract_rejects_empty_or_duplicate_selections() -> None:
    with pytest.raises(ValueError, match="typed selections"):
        SpecificIdentificationManifest("evt_sale", WALLET, MINT, ())
    duplicate = SpecificLotSelection("lot_same", 1)
    with pytest.raises(ValueError, match="repeat"):
        SpecificIdentificationManifest(
            "evt_sale",
            WALLET,
            MINT,
            (duplicate, duplicate),
        )
