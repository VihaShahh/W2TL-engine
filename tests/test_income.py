"""Hand-calculated reward, ordinary-airdrop, and spam receipt scenarios."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from engine.accounting import FifoLedger, account_income
from engine.core.exceptions import ExceptionCollector
from engine.models import (
    Classification,
    ClassificationType,
    Direction,
    FeeEvidence,
    IncomeRow,
    InventoryState,
    NormalizedEvent,
    PricePoint,
    ResolutionState,
    SourceReference,
    TokenLeg,
    TransactionStatus,
)
from engine.pipeline import WalletTaxEngine
from engine.policy.config import US_FIFO_POLICY

WALLET = "synthetic_wallet_income_test"
MINT = "synthetic_mint_reward"
TIMESTAMP = datetime(2025, 7, 1, 12, tzinfo=UTC)

def _event(
    classification_type: ClassificationType, *, quantity: int = 2_000_000
) -> NormalizedEvent:
    source = SourceReference(
        source_id=f"src_{classification_type.value}",
        fixture_set_id="focused_income_scenarios",
        wallet_id=WALLET,
        fixture_record_id=f"record_{classification_type.value}",
        signature=f"synthetic_signature_{classification_type.value}",
    )
    return NormalizedEvent(
        event_id=f"evt_{classification_type.value}",
        source_refs=(source,),
        timestamp_utc=TIMESTAMP,
        slot=1,
        instruction_index=0,
        wallet_id=WALLET,
        status=TransactionStatus.SUCCEEDED,
        program="synthetic_income",
        instruction_type=classification_type.value,
        token_legs=(
            TokenLeg(
                component_id=f"leg_{classification_type.value}",
                mint=MINT,
                symbol="RWD",
                decimals=6,
                direction=Direction.INBOUND,
                quantity_base_units=quantity,
            ),
        ),
        fee=FeeEvidence(
            payer_wallet_id=WALLET,
            fee_base_units=0,
            charged=False,
        ),
        cash_value_usd=None,
        counterparty_wallet_id=None,
    )

def _price(price_id: str, value: str) -> PricePoint:
    return PricePoint(
        price_id=price_id,
        mint=MINT,
        timestamp_utc=TIMESTAMP,
        valuation_currency="USD",
        price_usd=Decimal(value),
    )

@pytest.mark.parametrize(
    "classification_type",
    (ClassificationType.STAKING_REWARD, ClassificationType.AIRDROP),
)
def test_valued_income_creates_equal_income_and_lot_basis(
    classification_type: ClassificationType,
) -> None:
    ledger = FifoLedger()
    event = _event(classification_type)

    # Independent calculation: 2 RWD x USD 3.25 = USD 6.50.
    rows = account_income(
        event=event,
        classification_type=classification_type,
        prices=(_price("price_reward_3_25", "3.25"),),
        policy=US_FIFO_POLICY,
        ledger=ledger,
        collector=ExceptionCollector(),
    )
    row = rows[0]
    lot = ledger.get_lot(row.created_lot_id)

    assert row.category is classification_type
    assert row.quantity == Decimal("2.000000")
    assert row.price_usd == Decimal("3.25")
    assert row.price_source_id == "price_reward_3_25"
    assert row.fair_value_usd == Decimal("6.50000000")
    assert lot.source_event_id == event.event_id
    assert lot.starting_quantity_base_units == 2_000_000
    assert lot.starting_basis.require_total_usd() == row.fair_value_usd

@pytest.mark.parametrize(
    ("prices", "expected_code"),
    [
        ((), "missing_income_price"),
        (
            (
                _price("price_reward_a", "3.25"),
                _price("price_reward_b", "3.50"),
            ),
            "conflicting_income_price",
        ),
    ],
    ids=("missing", "conflicting"),
)
def test_unvalued_ordinary_receipt_never_fabricates_income(
    prices: tuple[PricePoint, ...], expected_code: str
) -> None:
    ledger = FifoLedger()
    collector = ExceptionCollector()

    rows = account_income(
        event=_event(ClassificationType.AIRDROP),
        classification_type=ClassificationType.AIRDROP,
        prices=prices,
        policy=US_FIFO_POLICY,
        ledger=ledger,
        collector=collector,
    )
    lot = ledger.open_lots()[0]
    exception = collector.items()[0]

    assert rows == ()
    assert exception.code == expected_code
    assert lot.starting_quantity_base_units == 2_000_000
    assert not lot.starting_basis.is_known
    assert lot.starting_basis.require_exception_id() == exception.exception_id

def test_unpriced_spam_is_quarantined_without_income_or_open_inventory() -> None:
    ledger = FifoLedger()
    collector = ExceptionCollector()
    event = _event(ClassificationType.SPAM_AIRDROP, quantity=9_000_000)
    income_rows: list[IncomeRow] = []

    rows = WalletTaxEngine(policy=US_FIFO_POLICY)._process_event(
        event=event,
        classification=Classification(
            event_id=event.event_id,
            type=ClassificationType.SPAM_AIRDROP,
            state=ResolutionState.REVIEW_FLAGGED,
            rule_id="core.spam_airdrop",
            evidence_source_ids=(event.source_refs[0].source_id,),
        ),
        prices=(),
        ledger=ledger,
        collector=collector,
        income_rows=income_rows,
        trace=None,
    )
    lot = ledger.all_lots()[0]
    exception = collector.items()[0]

    assert rows == ()
    assert income_rows == []
    assert exception.code == "unpriced_spam_airdrop"
    assert lot.state is InventoryState.QUARANTINED
    assert lot.starting_quantity_base_units == 9_000_000
    assert lot.starting_basis.require_exception_id() == exception.exception_id
    assert ledger.open_lots() == ()

def test_pipeline_appends_valued_reward_income_without_disposal() -> None:
    ledger = FifoLedger()
    event = _event(ClassificationType.STAKING_REWARD)
    income_rows: list[IncomeRow] = []

    disposal_rows = WalletTaxEngine(policy=US_FIFO_POLICY)._process_event(
        event=event,
        classification=Classification(
            event_id=event.event_id,
            type=ClassificationType.STAKING_REWARD,
            state=ResolutionState.RESOLVED,
            rule_id="core.staking_reward",
            evidence_source_ids=(event.source_refs[0].source_id,),
        ),
        prices=(_price("price_reward_3_25", "3.25"),),
        ledger=ledger,
        collector=ExceptionCollector(),
        income_rows=income_rows,
        trace=None,
    )

    assert disposal_rows == ()
    assert len(income_rows) == 1
    assert income_rows[0].fair_value_usd == Decimal("6.50")
    assert (
        ledger.get_lot(income_rows[0].created_lot_id)
        .starting_basis.require_total_usd()
        == income_rows[0].fair_value_usd
    )
