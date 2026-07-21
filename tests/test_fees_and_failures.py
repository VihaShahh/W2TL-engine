"""Hand-calculated event-context fee and failed-transaction scenarios."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from engine.accounting import FifoLedger
from engine.core.exceptions import ExceptionCollector
from engine.models import (
    Classification,
    ClassificationType,
    Direction,
    DisposalRow,
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

WALLET = "synthetic_wallet_fee_test"
SKITO = "synthetic_mint_skito"
NOVA = "synthetic_mint_nova"
SOL = "synthetic_mint_sol"
TIMESTAMP = datetime(2025, 8, 1, 12, tzinfo=UTC)

def _leg(
    *, mint: str, symbol: str, direction: Direction, quantity: int, decimals: int = 6
) -> TokenLeg:
    return TokenLeg(
        component_id=f"cmp_{mint}_{direction.value}_{quantity}",
        mint=mint,
        symbol=symbol,
        decimals=decimals,
        direction=direction,
        quantity_base_units=quantity,
    )

def _event(
    event_id: str,
    *,
    instruction_type: str,
    legs: tuple[TokenLeg, ...],
    cash_value_usd: Decimal | None = None,
    status: TransactionStatus = TransactionStatus.SUCCEEDED,
    fee_mint: str | None = SOL,
) -> NormalizedEvent:
    source = SourceReference(
        source_id=f"src_{event_id}",
        fixture_set_id="focused_fee_scenarios",
        wallet_id=WALLET,
        fixture_record_id=f"record_{event_id}",
        signature=f"synthetic_signature_{event_id}",
    )
    charged = fee_mint is not None
    return NormalizedEvent(
        event_id=event_id,
        source_refs=(source,),
        timestamp_utc=TIMESTAMP,
        slot=1,
        instruction_index=0,
        wallet_id=WALLET,
        status=status,
        program="synthetic_fee_program",
        instruction_type=instruction_type,
        token_legs=legs,
        fee=FeeEvidence(
            payer_wallet_id=WALLET,
            fee_base_units=10_000_000 if charged else 0,
            charged=charged,
            mint=fee_mint,
            symbol=("SOL" if fee_mint == SOL else "NOVA") if charged else None,
            decimals=9 if fee_mint == SOL else (6 if charged else None),
        ),
        cash_value_usd=cash_value_usd,
        counterparty_wallet_id=None,
    )

def _seed(
    ledger: FifoLedger,
    *,
    event_id: str,
    mint: str,
    symbol: str,
    quantity: int,
    basis: str,
    decimals: int = 6,
) -> None:
    event = _event(
        event_id,
        instruction_type="buy",
        legs=(
            _leg(
                mint=mint,
                symbol=symbol,
                direction=Direction.INBOUND,
                quantity=quantity,
                decimals=decimals,
            ),
        ),
        cash_value_usd=Decimal(basis),
        fee_mint=None,
    )
    ledger.acquire(event)

def _price(mint: str, value: str) -> PricePoint:
    return PricePoint(
        price_id=f"price_{mint}_{value}",
        mint=mint,
        timestamp_utc=TIMESTAMP,
        valuation_currency="USD",
        price_usd=Decimal(value),
    )

def _classification(event: NormalizedEvent, type: ClassificationType) -> Classification:
    return Classification(
        event_id=event.event_id,
        type=type,
        state=ResolutionState.RESOLVED,
        rule_id=f"focused.{type.value}",
        evidence_source_ids=(event.source_refs[0].source_id,),
    )

def _process(
    *,
    event: NormalizedEvent,
    type: ClassificationType,
    prices: tuple[PricePoint, ...],
    ledger: FifoLedger,
    collector: ExceptionCollector,
) -> tuple[DisposalRow, ...]:
    return WalletTaxEngine(policy=US_FIFO_POLICY)._process_event(
        event=event,
        classification=_classification(event, type),
        prices=prices,
        ledger=ledger,
        collector=collector,
        income_rows=[],
        trace=None,
    )

def test_third_token_acquisition_fee_adjusts_basis_once_and_reports_fee_disposal() -> None:
    ledger = FifoLedger()
    _seed(
        ledger,
        event_id="seed_sol",
        mint=SOL,
        symbol="SOL",
        quantity=20_000_000,
        basis="0.80",
        decimals=9,
    )
    event = _event(
        "buy_nova",
        instruction_type="buy",
        legs=(
            _leg(
                mint=NOVA,
                symbol="NOVA",
                direction=Direction.INBOUND,
                quantity=2_000_000,
            ),
        ),
        cash_value_usd=Decimal("20"),
    )

    rows = _process(
        event=event,
        type=ClassificationType.BUY,
        prices=(_price(SOL, "100"),),
        ledger=ledger,
        collector=ExceptionCollector(),
    )
    nova_lot = next(lot for lot in ledger.open_lots() if lot.mint == NOVA)

    assert nova_lot.starting_basis.require_total_usd() == Decimal("21.000000000")
    assert len(rows) == 1
    assert rows[0].mint == SOL
    assert rows[0].proceeds_usd == Decimal("1.000000000")
    assert rows[0].cost_basis_usd == Decimal("0.400")
    assert rows[0].gain_loss_usd == Decimal("0.600000000")

def test_third_token_disposal_fee_reduces_main_proceeds_once() -> None:
    ledger = FifoLedger()
    _seed(
        ledger,
        event_id="seed_skito",
        mint=SKITO,
        symbol="SKITO",
        quantity=2_000_000,
        basis="10",
    )
    _seed(
        ledger,
        event_id="seed_sol_sale",
        mint=SOL,
        symbol="SOL",
        quantity=20_000_000,
        basis="0.80",
        decimals=9,
    )
    event = _event(
        "sell_skito",
        instruction_type="sell",
        legs=(
            _leg(
                mint=SKITO,
                symbol="SKITO",
                direction=Direction.OUTBOUND,
                quantity=2_000_000,
            ),
        ),
        cash_value_usd=Decimal("30"),
    )

    rows = _process(
        event=event,
        type=ClassificationType.SELL,
        prices=(_price(SOL, "100"),),
        ledger=ledger,
        collector=ExceptionCollector(),
    )
    main_row = next(row for row in rows if row.mint == SKITO)
    fee_row = next(row for row in rows if row.mint == SOL)

    assert main_row.proceeds_usd == Decimal("29.000000000")
    assert main_row.cost_basis_usd == Decimal("10")
    assert fee_row.proceeds_usd == Decimal("1.000000000")
    assert Decimal("30") - main_row.proceeds_usd == fee_row.proceeds_usd

def test_missing_fee_price_consumes_quantity_and_blocks_acquisition_basis() -> None:
    ledger = FifoLedger()
    _seed(
        ledger,
        event_id="seed_sol_missing_price",
        mint=SOL,
        symbol="SOL",
        quantity=20_000_000,
        basis="0.80",
        decimals=9,
    )
    event = _event(
        "buy_without_fee_price",
        instruction_type="buy",
        legs=(
            _leg(
                mint=NOVA,
                symbol="NOVA",
                direction=Direction.INBOUND,
                quantity=2_000_000,
            ),
        ),
        cash_value_usd=Decimal("20"),
    )
    collector = ExceptionCollector()

    rows = _process(
        event=event,
        type=ClassificationType.BUY,
        prices=(),
        ledger=ledger,
        collector=collector,
    )
    nova_lot = next(lot for lot in ledger.open_lots() if lot.mint == NOVA)
    remaining_sol = next(lot for lot in ledger.open_lots() if lot.mint == SOL)

    assert rows == ()
    assert collector.items()[0].code == "missing_fee_price"
    assert not nova_lot.starting_basis.is_known
    assert remaining_sol.remaining_quantity_base_units == 10_000_000

def test_same_token_fee_is_excepted_without_guessing_gross_quantity() -> None:
    ledger = FifoLedger()
    event = _event(
        "buy_with_overlapping_fee",
        instruction_type="buy",
        legs=(
            _leg(
                mint=NOVA,
                symbol="NOVA",
                direction=Direction.INBOUND,
                quantity=20_000_000,
            ),
        ),
        cash_value_usd=Decimal("20"),
        fee_mint=NOVA,
    )
    collector = ExceptionCollector()

    rows = _process(
        event=event,
        type=ClassificationType.BUY,
        prices=(_price(NOVA, "1"),),
        ledger=ledger,
        collector=collector,
    )
    lot = ledger.open_lots()[0]

    assert rows == ()
    assert collector.items()[0].code == "unsupported_fee_token_overlap"
    assert lot.remaining_quantity_base_units == 20_000_000
    assert not lot.starting_basis.is_known

def test_failed_swap_keeps_reverted_legs_absent_and_reports_proven_fee_only() -> None:
    ledger = FifoLedger()
    _seed(
        ledger,
        event_id="seed_sol_failed",
        mint=SOL,
        symbol="SOL",
        quantity=20_000_000,
        basis="0.80",
        decimals=9,
    )
    event = _event(
        "failed_swap",
        instruction_type="swap",
        legs=(
            _leg(
                mint=SKITO,
                symbol="SKITO",
                direction=Direction.OUTBOUND,
                quantity=2_000_000,
            ),
            _leg(
                mint=NOVA,
                symbol="NOVA",
                direction=Direction.INBOUND,
                quantity=4_000_000,
            ),
        ),
        status=TransactionStatus.FAILED,
    )

    rows = _process(
        event=event,
        type=ClassificationType.FAILED,
        prices=(_price(SOL, "100"),),
        ledger=ledger,
        collector=ExceptionCollector(),
    )

    assert len(rows) == 1 and rows[0].mint == SOL
    assert {lot.mint for lot in ledger.all_lots()} == {SOL}
    assert ledger.open_lots()[0].remaining_quantity_base_units == 10_000_000

def test_failed_transaction_without_charged_fee_has_no_accounting_effect() -> None:
    ledger = FifoLedger()
    event = _event(
        "failed_without_fee",
        instruction_type="swap",
        legs=(
            _leg(
                mint=SKITO,
                symbol="SKITO",
                direction=Direction.OUTBOUND,
                quantity=2_000_000,
            ),
            _leg(
                mint=NOVA,
                symbol="NOVA",
                direction=Direction.INBOUND,
                quantity=4_000_000,
            ),
        ),
        status=TransactionStatus.FAILED,
        fee_mint=None,
    )
    collector = ExceptionCollector()

    rows = _process(
        event=event,
        type=ClassificationType.FAILED,
        prices=(),
        ledger=ledger,
        collector=collector,
    )

    assert rows == ()
    assert ledger.all_lots() == ()
    assert collector.items() == ()
