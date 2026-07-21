"""Hand-calculated valued and blocked swap accounting scenarios."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from engine.accounting import FifoLedger, account_swap
from engine.core.exceptions import ExceptionCollector
from engine.country_US import UsTaxReportHandler
from engine.models import (
    Classification,
    ClassificationType,
    Direction,
    FeeEvidence,
    NormalizedEvent,
    PricePoint,
    ResolutionState,
    SourceReference,
    TokenLeg,
    TransactionStatus,
)
from engine.pipeline import WalletTaxEngine
from engine.policy.config import US_FIFO_POLICY

WALLET = "synthetic_wallet_swap_test"
SKITO = "synthetic_mint_skito"
NOVA = "synthetic_mint_nova"
TIMESTAMP = datetime(2025, 6, 1, 12, tzinfo=UTC)

def _source(record_id: str) -> SourceReference:
    return SourceReference(
        source_id=f"src_{record_id}",
        fixture_set_id="focused_swap_scenarios",
        wallet_id=WALLET,
        fixture_record_id=record_id,
        signature=f"synthetic_signature_{record_id}",
    )

def _leg(
    component_id: str,
    mint: str,
    symbol: str,
    direction: Direction,
    quantity: int,
) -> TokenLeg:
    return TokenLeg(
        component_id=component_id,
        mint=mint,
        symbol=symbol,
        decimals=6,
        direction=direction,
        quantity_base_units=quantity,
    )

def _event(
    event_id: str,
    *,
    token_legs: tuple[TokenLeg, ...],
    timestamp: datetime = TIMESTAMP,
    cash_value_usd: Decimal | None = None,
    fee_charged: bool = False,
) -> NormalizedEvent:
    return NormalizedEvent(
        event_id=event_id,
        source_refs=(_source(event_id),),
        timestamp_utc=timestamp,
        slot=1,
        instruction_index=0,
        wallet_id=WALLET,
        status=TransactionStatus.SUCCEEDED,
        program="synthetic_swap",
        instruction_type="swap",
        token_legs=token_legs,
        fee=FeeEvidence(
            payer_wallet_id=WALLET,
            fee_base_units=5_000 if fee_charged else 0,
            charged=fee_charged,
            mint="synthetic_mint_sol" if fee_charged else None,
            symbol="SOL" if fee_charged else None,
            decimals=9 if fee_charged else None,
        ),
        cash_value_usd=cash_value_usd,
        counterparty_wallet_id=None,
    )

def _price(price_id: str, mint: str, value: str) -> PricePoint:
    return PricePoint(
        price_id=price_id,
        mint=mint,
        timestamp_utc=TIMESTAMP,
        valuation_currency="USD",
        price_usd=Decimal(value),
    )

def _seed_known_skito(ledger: FifoLedger) -> None:
    # Independent ground truth: 10 SKITO cost USD 40, so 3 SKITO carry USD 12 basis.
    ledger.acquire(
        _event(
            "evt_seed_known",
            timestamp=datetime(2025, 1, 1, 12, tzinfo=UTC),
            token_legs=(_leg("leg_seed_known", SKITO, "SKITO", Direction.INBOUND, 10_000_000),),
            cash_value_usd=Decimal("40"),
        )
    )

def _swap_event(*, fee_charged: bool = False) -> NormalizedEvent:
    return _event(
        "evt_swap",
        token_legs=(
            _leg("leg_receive_nova", NOVA, "NOVA", Direction.INBOUND, 6_000_000),
            _leg("leg_give_skito", SKITO, "SKITO", Direction.OUTBOUND, 3_000_000),
        ),
        fee_charged=fee_charged,
    )

def test_agreeing_swap_links_disposal_acquisition_fee_and_exact_value() -> None:
    ledger = FifoLedger()
    _seed_known_skito(ledger)
    event = _swap_event(fee_charged=True)
    collector = ExceptionCollector()

    # Hand calculation: 3 SKITO x USD 7 = 6 NOVA x USD 3.50 = USD 21.
    swap = account_swap(
        event=event,
        prices=(
            _price("price_skito_7", SKITO, "7"),
            _price("price_nova_3_50", NOVA, "3.50"),
        ),
        policy=US_FIFO_POLICY,
        ledger=ledger,
        collector=collector,
    )
    rows = UsTaxReportHandler(policy=US_FIFO_POLICY).prepare_disposal_rows(
        event=event,
        consumptions=swap.consumptions,
        ledger=ledger,
        leg=swap.given_leg,
        proceeds_usd=swap.exchange_value_usd,
    )

    assert swap.is_valued
    assert swap.exchange_value_usd == Decimal("21.00")
    assert swap.price_source_ids == ("price_nova_3_50", "price_skito_7")
    assert swap.fee is event.fee
    assert swap.fee.charged and swap.fee.fee_base_units == 5_000
    assert swap.consumptions[0].disposal_event_id == event.event_id
    assert swap.consumptions[0].consumed_basis.require_total_usd() == Decimal("12")
    assert swap.received_lot.source_event_id == event.event_id
    assert swap.received_lot.starting_quantity_base_units == 6_000_000
    assert swap.received_lot.starting_basis.require_total_usd() == Decimal("21.00")
    assert len(rows) == 1
    assert rows[0].event_id == event.event_id
    assert rows[0].proceeds_usd == Decimal("21.00")
    assert rows[0].cost_basis_usd == Decimal("12")
    assert rows[0].gain_loss_usd == Decimal("9.00")
    assert collector.items() == ()

@pytest.mark.parametrize(
    ("prices", "expected_code"),
    [
        ((_price("price_skito_7", SKITO, "7"),), "one_sided_swap_valuation"),
        ((), "missing_swap_valuation"),
        (
            (
                _price("price_skito_7_a", SKITO, "7"),
                _price("price_skito_7_b", SKITO, "7"),
                _price("price_nova_3_50", NOVA, "3.50"),
            ),
            "conflicting_swap_valuation",
        ),
        (
            (
                _price("price_skito_7", SKITO, "7"),
                _price("price_nova_4", NOVA, "4"),
            ),
            "conflicting_swap_valuation",
        ),
    ],
    ids=("one-sided", "missing", "source-conflict", "value-disagreement"),
)
def test_unvalued_swap_consumes_given_units_and_preserves_received_quantity(
    prices: tuple[PricePoint, ...], expected_code: str
) -> None:
    ledger = FifoLedger()
    _seed_known_skito(ledger)
    event = _swap_event()
    collector = ExceptionCollector()

    swap = account_swap(
        event=event,
        prices=prices,
        policy=US_FIFO_POLICY,
        ledger=ledger,
        collector=collector,
    )
    exception = collector.items()[0]

    assert not swap.is_valued
    assert swap.exchange_value_usd is None
    assert exception.code == expected_code
    assert swap.valuation_exception_id == exception.exception_id
    assert swap.consumptions[0].quantity_base_units == 3_000_000
    assert swap.received_lot.starting_quantity_base_units == 6_000_000
    assert not swap.received_lot.starting_basis.is_known
    assert swap.received_lot.starting_basis.require_exception_id() == exception.exception_id
    assert (
        sum(lot.remaining_quantity_base_units for lot in ledger.open_lots() if lot.mint == SKITO)
        == 7_000_000
    )

def test_valued_swap_with_insufficient_basis_preserves_known_received_basis() -> None:
    ledger = FifoLedger()
    seed_exception_id = "exc_missing_original_acquisition"
    ledger.acquire_unknown(
        _event(
            "evt_seed_unknown",
            timestamp=datetime(2025, 1, 1, 12, tzinfo=UTC),
            token_legs=(_leg("leg_seed_unknown", SKITO, "SKITO", Direction.INBOUND, 3_000_000),),
        ),
        exception_id=seed_exception_id,
    )
    event = _swap_event()

    swap = account_swap(
        event=event,
        prices=(
            _price("price_skito_7", SKITO, "7"),
            _price("price_nova_3_50", NOVA, "3.50"),
        ),
        policy=US_FIFO_POLICY,
        ledger=ledger,
        collector=ExceptionCollector(),
    )
    rows = UsTaxReportHandler(policy=US_FIFO_POLICY).prepare_disposal_rows(
        event=event,
        consumptions=swap.consumptions,
        ledger=ledger,
        leg=swap.given_leg,
        proceeds_usd=swap.exchange_value_usd,
    )

    assert swap.is_valued
    assert not swap.consumptions[0].consumed_basis.is_known
    assert swap.consumptions[0].consumed_basis.require_exception_id() == seed_exception_id
    assert swap.received_lot.starting_basis.require_total_usd() == Decimal("21.00")
    assert rows == ()

def test_pipeline_links_insufficient_basis_exception_to_valued_swap() -> None:
    ledger = FifoLedger()
    seed_exception_id = "exc_missing_original_acquisition"
    ledger.acquire_unknown(
        _event(
            "evt_pipeline_seed_unknown",
            timestamp=datetime(2025, 1, 1, 12, tzinfo=UTC),
            token_legs=(
                _leg(
                    "leg_pipeline_seed_unknown",
                    SKITO,
                    "SKITO",
                    Direction.INBOUND,
                    3_000_000,
                ),
            ),
        ),
        exception_id=seed_exception_id,
    )
    event = _swap_event()
    collector = ExceptionCollector()

    rows = WalletTaxEngine(policy=US_FIFO_POLICY)._process_event(
        event=event,
        classification=Classification(
            event_id=event.event_id,
            type=ClassificationType.SWAP,
            state=ResolutionState.RESOLVED,
            rule_id="core.swap",
            evidence_source_ids=(event.source_refs[0].source_id,),
        ),
        prices=(
            _price("price_skito_7", SKITO, "7"),
            _price("price_nova_3_50", NOVA, "3.50"),
        ),
        ledger=ledger,
        collector=collector,
        income_rows=[],
        trace=None,
    )
    nova_lot = next(lot for lot in ledger.open_lots() if lot.mint == NOVA)
    exception = collector.items()[0]

    assert rows == ()
    assert exception.code == "unknown_basis_disposal_slice"
    assert seed_exception_id in exception.related_object_ids
    assert nova_lot.starting_quantity_base_units == 6_000_000
    assert nova_lot.starting_basis.require_total_usd() == Decimal("21.00")
