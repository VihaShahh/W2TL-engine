"""Report-adapter, exception-output, and whole-run invariant checks."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from engine.accounting import (
    FifoLedger,
    ReconciliationError,
    account_income,
    reconcile_run,
)
from engine.core.exceptions import ExceptionCollector
from engine.country_US import UsTaxReportHandler
from engine.models import (
    ClassificationType,
    Direction,
    DisposalRow,
    FeeEvidence,
    NormalizedEvent,
    PricePoint,
    SourceReference,
    TokenLeg,
    TransactionStatus,
)
from engine.pipeline import run_pipeline
from engine.policy.config import US_FIFO_POLICY
from engine.reporting.writer import write_reports

WALLET = "synthetic_wallet_report_test"
MINT = "synthetic_mint_report_asset"

def _event(
    event_id: str,
    *,
    timestamp: datetime,
    direction: Direction,
    quantity_base_units: int,
    cash_value_usd: Decimal | None,
) -> NormalizedEvent:
    source = SourceReference(
        source_id=f"src_{event_id}",
        fixture_set_id="focused_report_scenarios",
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
        program="synthetic_report_program",
        instruction_type="buy" if direction is Direction.INBOUND else "sell",
        token_legs=(
            TokenLeg(
                component_id=f"leg_{event_id}",
                mint=MINT,
                symbol="RPT",
                decimals=6,
                direction=direction,
                quantity_base_units=quantity_base_units,
            ),
        ),
        fee=FeeEvidence(payer_wallet_id=WALLET, fee_base_units=0, charged=False),
        cash_value_usd=cash_value_usd,
        counterparty_wallet_id=None,
    )

def _two_lot_disposal() -> tuple[FifoLedger, tuple[DisposalRow, ...]]:
    ledger = FifoLedger()
    ledger.acquire(
        _event(
            "evt_buy_old",
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            direction=Direction.INBOUND,
            quantity_base_units=1_000_000,
            cash_value_usd=Decimal("2"),
        )
    )
    ledger.acquire(
        _event(
            "evt_buy_new",
            timestamp=datetime(2025, 1, 2, tzinfo=UTC),
            direction=Direction.INBOUND,
            quantity_base_units=2_000_000,
            cash_value_usd=Decimal("6"),
        )
    )
    sale = _event(
        "evt_sell_two_lots",
        timestamp=datetime(2025, 3, 1, tzinfo=UTC),
        direction=Direction.OUTBOUND,
        quantity_base_units=2_000_000,
        cash_value_usd=Decimal("10"),
    )
    consumptions = ledger.consume(sale)
    rows = UsTaxReportHandler(policy=US_FIFO_POLICY).prepare_disposal_rows(
        event=sale,
        consumptions=consumptions,
        ledger=ledger,
    )
    return ledger, rows

def test_us_adapter_creates_stable_rows_that_reconcile_to_two_lot_parent() -> None:
    ledger, rows = _two_lot_disposal()

    assert len(rows) == 2
    assert len({row.disposal_id for row in rows}) == 2
    assert sum(row.quantity_base_units for row in rows) == 2_000_000
    assert sum(row.proceeds_usd for row in rows) == Decimal("10")
    assert {row.term.value for row in rows} == {"long", "short"}
    assert all(
        row.gain_loss_usd == row.proceeds_usd - row.cost_basis_usd
        for row in rows
    )
    result = reconcile_run(ledger=ledger, disposals=rows, income=())
    assert result.disposal_count == 2
    assert result.consumption_count == 2

def test_income_adapter_reconciles_receipt_value_to_created_lot() -> None:
    event = _event(
        "evt_reward",
        timestamp=datetime(2025, 4, 1, tzinfo=UTC),
        direction=Direction.INBOUND,
        quantity_base_units=2_000_000,
        cash_value_usd=None,
    )
    ledger = FifoLedger()
    income = account_income(
        event=event,
        classification_type=ClassificationType.STAKING_REWARD,
        prices=(
            PricePoint(
                price_id="price_reward_exact",
                mint=MINT,
                timestamp_utc=event.timestamp_utc,
                valuation_currency="USD",
                price_usd=Decimal("3.25"),
            ),
        ),
        policy=US_FIFO_POLICY,
        ledger=ledger,
        collector=ExceptionCollector(),
    )
    rows = UsTaxReportHandler(policy=US_FIFO_POLICY).prepare_income_rows(income=income)

    assert len(rows) == 1
    assert rows[0].fair_value_usd == Decimal("6.50000000")
    assert rows[0].price_source_id == "price_reward_exact"
    assert (
        ledger.get_lot(rows[0].created_lot_id).starting_basis.require_total_usd()
        == rows[0].fair_value_usd
    )
    assert reconcile_run(ledger=ledger, disposals=(), income=rows).income_count == 1

def test_reconciliation_blocks_a_corrupted_disposal_before_writing() -> None:
    ledger, rows = _two_lot_disposal()
    corrupted = (replace(rows[0], gain_loss_usd=Decimal("999")), rows[1])

    with pytest.raises(ReconciliationError, match="proceeds minus basis"):
        reconcile_run(ledger=ledger, disposals=corrupted, income=())

def test_writer_rejects_non_finite_money(tmp_path: Path) -> None:
    _, rows = _two_lot_disposal()
    corrupted = (replace(rows[0], proceeds_usd=Decimal("NaN")), rows[1])

    with pytest.raises(ValueError, match="finite Decimal"):
        write_reports(
            output_dir=tmp_path,
            fixture_set_id="focused_report_scenarios",
            policy=US_FIFO_POLICY,
            disposals=corrupted,
            income=(),
            exceptions=(),
        )

def test_exception_json_keeps_every_actionable_field_and_stable_order(tmp_path: Path) -> None:
    run_pipeline(
        fixtures_dir=Path("fixtures/complete"),
        output_dir=tmp_path,
        policy=US_FIFO_POLICY,
    )
    payload = json.loads((tmp_path / "exceptions.json").read_text(encoding="utf-8"))
    exceptions = payload["exceptions"]

    assert [item["exception_id"] for item in exceptions] == sorted(
        item["exception_id"] for item in exceptions
    )
    for item in exceptions:
        assert item["code"]
        assert item["severity"] in {
            "warning",
            "event_blocking",
            "monetary_output_blocking",
            "run_fatal",
        }
        assert item["stage"]
        assert item["source_refs"]
        assert item["observed_facts"]
        assert item["reason"]
        assert item["affected_outputs"]
        assert item["human_request"]
