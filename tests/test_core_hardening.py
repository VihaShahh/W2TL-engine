"""Focused coverage, lineage, determinism, and offline checks."""

from __future__ import annotations

import csv
import hashlib
import json
import socket
from dataclasses import fields
from pathlib import Path
from typing import Any, ClassVar

import pytest

from engine.accounting import FifoLedger
from engine.country_US import UsTaxReportHandler
from engine.evidence.loader import load_fixture
from engine.evidence.processor import process_evidence
from engine.interfaces.cli import main
from engine.models import NormalizedEvent
from engine.pipeline import WalletTaxEngine, run_pipeline
from engine.policy.config import US_FIFO_POLICY

FIXTURE_DIR = Path("fixtures/complete")
OUTPUT_NAMES = ("disposals.csv", "income.csv", "exceptions.json")

def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source_file:
        return list(csv.DictReader(source_file))

class CapturingLedger(FifoLedger):
    latest: ClassVar[CapturingLedger | None] = None

    def __init__(self) -> None:
        super().__init__()
        type(self).latest = self

def test_complete_scenarios_document_every_named_naive_failure() -> None:
    fixture = load_fixture(FIXTURE_DIR, policy=US_FIFO_POLICY)

    assert {scenario.scenario_id for scenario in fixture.scenarios} == {
        "external-and-unknown-basis",
        "fees-and-failures",
        "fifo-sales-and-holding",
        "foundational-buys",
        "income-and-spam",
        "ordering-duplicates-and-invalid-evidence",
        "owned-wallet-transfers",
        "swaps-and-wraps",
    }
    for scenario in fixture.scenarios:
        assert scenario.source_record_ids
        assert scenario.purpose.strip()
        assert scenario.naive_failure.strip()
        assert scenario.expected_outcome.strip()

def test_normalized_events_remain_free_of_report_and_lot_fields() -> None:
    neutral_fields = {field.name for field in fields(NormalizedEvent)}

    assert neutral_fields.isdisjoint(
        {
            "consumption_id",
            "cost_basis_usd",
            "created_lot_id",
            "date_acquired",
            "date_disposed",
            "disposal_id",
            "fair_value_usd",
            "gain_loss_usd",
            "income_id",
            "lot_id",
            "proceeds_usd",
            "term",
        }
    )

def test_us_adapter_does_not_mutate_complete_accounting_projection(tmp_path: Path) -> None:
    fixture = load_fixture(FIXTURE_DIR, policy=US_FIFO_POLICY)
    evidence = process_evidence(fixture)
    WalletTaxEngine(policy=US_FIFO_POLICY, ledger_class=CapturingLedger).run(
        fixtures_dir=FIXTURE_DIR,
        output_dir=tmp_path,
    )
    ledger = CapturingLedger.latest
    assert ledger is not None
    event_by_id = {event.event_id: event for event in evidence.events}
    disposal_event_id = next(
        item.disposal_event_id
        for item in ledger.all_consumptions()
        if item.disposal_event_id in event_by_id
    )
    consumptions = tuple(
        item
        for item in ledger.all_consumptions()
        if item.disposal_event_id == disposal_event_id
    )
    before = (ledger.all_lots(), ledger.all_consumptions(), ledger.all_movements())

    rows = UsTaxReportHandler(policy=US_FIFO_POLICY).prepare_disposal_rows(
        event=event_by_id[disposal_event_id],
        consumptions=consumptions,
        ledger=ledger,
    )

    assert rows
    assert (ledger.all_lots(), ledger.all_consumptions(), ledger.all_movements()) == before

def test_complete_exceptions_are_actionable_and_source_linked(tmp_path: Path) -> None:
    fixture = load_fixture(FIXTURE_DIR, policy=US_FIFO_POLICY)
    source_ids = {source.source_id for source in fixture.all_source_refs}
    run_pipeline(fixtures_dir=FIXTURE_DIR, output_dir=tmp_path, policy=US_FIFO_POLICY)
    exceptions = _load_json(tmp_path / "exceptions.json")["exceptions"]

    assert len(exceptions) == 24
    for exception in exceptions:
        assert exception["code"]
        assert exception["reason"]
        assert exception["observed_facts"]
        assert exception["affected_outputs"]
        assert exception["human_request"]
        assert {
            source["source_id"] for source in exception["source_refs"]
        } <= source_ids

def test_every_complete_result_has_valid_source_lineage(tmp_path: Path) -> None:
    fixture = load_fixture(FIXTURE_DIR, policy=US_FIFO_POLICY)
    evidence = process_evidence(fixture)
    WalletTaxEngine(policy=US_FIFO_POLICY, ledger_class=CapturingLedger).run(
        fixtures_dir=FIXTURE_DIR,
        output_dir=tmp_path,
    )
    ledger = CapturingLedger.latest
    assert ledger is not None

    source_ids = {source.source_id for source in fixture.all_source_refs}
    event_ids = {event.event_id for event in evidence.events}
    lot_ids = {lot.lot_id for lot in ledger.all_lots()}
    consumption_ids = {
        consumption.consumption_id for consumption in ledger.all_consumptions()
    }

    assert {entry.source.source_id for entry in evidence.coverage} == source_ids
    assert len(evidence.coverage) == len(source_ids)
    for event, classification in zip(
        evidence.events, evidence.classifications, strict=True
    ):
        event_source_ids = {source.source_id for source in event.source_refs}
        assert event_source_ids
        assert event_source_ids <= source_ids
        assert classification.event_id == event.event_id
        assert set(classification.evidence_source_ids) == event_source_ids

    for lot in ledger.all_lots():
        assert lot.source_event_id in event_ids
        assert lot.parent_lot_id is None or lot.parent_lot_id in lot_ids
    for movement in ledger.all_movements():
        assert movement.event_id in event_ids
        assert movement.source_lot_id in lot_ids
        assert movement.destination_lot_id in lot_ids
    for consumption in ledger.all_consumptions():
        assert consumption.lot_id in lot_ids

    disposal_rows = _read_csv(tmp_path / "disposals.csv")
    income_rows = _read_csv(tmp_path / "income.csv")
    for row in disposal_rows:
        assert row["source_id"] in source_ids
        assert row["lot_id"] in lot_ids
        assert row["consumption_id"] in consumption_ids
    for row in income_rows:
        assert row["source_id"] in source_ids
        assert row["event_id"] in event_ids
        assert row["created_lot_id"] in lot_ids
        assert row["price_source_id"] in {price.price_id for price in fixture.prices}

def test_complete_cli_is_byte_stable_across_clean_output_directories(
    tmp_path: Path,
) -> None:
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    common_args = [
        "run",
        "--fixtures",
        str(FIXTURE_DIR),
        "--policy-id",
        US_FIFO_POLICY.policy_id,
        "--policy-version",
        str(US_FIFO_POLICY.policy_version),
    ]

    assert main([*common_args, "--output", str(first_output)]) == 0
    assert main([*common_args, "--output", str(second_output)]) == 0

    for name in OUTPUT_NAMES:
        first = (first_output / name).read_bytes()
        second = (second_output / name).read_bytes()
        assert first == second
        assert hashlib.sha256(first).digest() == hashlib.sha256(second).digest()

def test_complete_core_opens_no_network_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_on_socket(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"Core attempted network access: args={args!r}, kwargs={kwargs!r}")

    monkeypatch.setattr(socket, "socket", fail_on_socket)

    result = run_pipeline(
        fixtures_dir=FIXTURE_DIR,
        output_dir=tmp_path,
        policy=US_FIFO_POLICY,
    )

    assert result.reconciliation_passed
    assert all((tmp_path / name).is_file() for name in OUTPUT_NAMES)
