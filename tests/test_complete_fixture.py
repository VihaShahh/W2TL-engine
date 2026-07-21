"""Complete fixture volume, ground truth, lineage, and privacy checks."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, ClassVar

import pytest

from engine.accounting import FifoLedger
from engine.evidence.loader import load_fixture
from engine.evidence.processor import process_evidence
from engine.models import Basis, Lot
from engine.pipeline import WalletTaxEngine
from engine.policy.config import US_FIFO_POLICY

FIXTURE_DIR = Path("fixtures/complete")
GROUND_TRUTH_DIR = FIXTURE_DIR / "ground_truth"

def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

def _money(basis: Basis) -> str | None:
    return format(basis.require_total_usd(), "f") if basis.is_known else None

def _lot_record(lot: Lot) -> dict[str, object]:
    result: dict[str, object] = {
        "lot_id": lot.lot_id,
        "source_event_id": lot.source_event_id,
        "parent_lot_id": lot.parent_lot_id,
        "wallet_id": lot.wallet_id,
        "mint": lot.mint,
        "symbol": lot.symbol,
        "starting_quantity_base_units": lot.starting_quantity_base_units,
        "remaining_quantity_base_units": lot.remaining_quantity_base_units,
        "state": lot.state.value,
        "acquisition_state": lot.acquisition.state.value,
        "acquisition_timestamp": lot.acquisition.sort_timestamp_utc.isoformat().replace(
            "+00:00", "Z"
        ),
        "basis_state": lot.starting_basis.state.value,
        "starting_basis_usd": _money(lot.starting_basis),
        "remaining_basis_usd": _money(lot.remaining_basis),
    }
    if not lot.starting_basis.is_known:
        result["basis_exception_id"] = lot.starting_basis.require_exception_id()
    if not lot.acquisition.is_known:
        result["acquisition_exception_id"] = lot.acquisition.require_exception_id()
    return result

class CapturingLedger(FifoLedger):
    latest: ClassVar[CapturingLedger | None] = None

    def __init__(self) -> None:
        super().__init__()
        type(self).latest = self

def test_complete_fixture_has_96_mapped_records_and_required_event_families() -> None:
    fixture = load_fixture(FIXTURE_DIR, policy=US_FIFO_POLICY)
    evidence = process_evidence(fixture)
    mapped = Counter(
        record_id for scenario in fixture.scenarios for record_id in scenario.source_record_ids
    )

    assert len(fixture.all_source_refs) == 96
    assert 80 <= len(fixture.all_source_refs) <= 120
    assert set(mapped.values()) == {1}
    assert set(mapped) == {source.fixture_record_id for source in fixture.all_source_refs}
    classifications = Counter(item.type.value for item in evidence.classifications)
    assert classifications == {
        "airdrop": 3,
        "buy": 20,
        "external_inbound": 8,
        "external_outbound": 2,
        "failed": 4,
        "fee": 2,
        "internal_transfer": 6,
        "sell": 19,
        "spam_airdrop": 3,
        "staking_reward": 4,
        "swap": 12,
        "wrap": 2,
    }
    assert Counter(item.status.value for item in evidence.coverage) == {
        "quarantined": 4,
        "resolved": 82,
        "review_flagged": 3,
        "secondary_view": 7,
    }

def test_complete_classifications_match_frozen_stable_id_ground_truth() -> None:
    fixture = load_fixture(FIXTURE_DIR, policy=US_FIFO_POLICY)
    evidence = process_evidence(fixture)
    actual = [
        {
            "fixture_record_ids": [source.fixture_record_id for source in event.source_refs],
            "source_ids": list(classification.evidence_source_ids),
            "event_id": event.event_id,
            "type": classification.type.value,
            "state": classification.state.value,
            "rule_id": classification.rule_id,
        }
        for event, classification in zip(evidence.events, evidence.classifications, strict=True)
    ]

    assert actual == _load_json(GROUND_TRUTH_DIR / "classifications.json")["classifications"]

def test_complete_minefields_keep_expected_order_state_and_partial_linkage() -> None:
    fixture = load_fixture(FIXTURE_DIR, policy=US_FIFO_POLICY)
    evidence = process_evidence(fixture)
    event_by_record = {
        source.fixture_record_id: event for event in evidence.events for source in event.source_refs
    }
    event_positions = {
        source.fixture_record_id: index
        for index, event in enumerate(evidence.events)
        for source in event.source_refs
    }
    coverage = {item.source.fixture_record_id: item.status.value for item in evidence.coverage}

    assert event_positions["complete-buy-tie-alpha"] < event_positions["complete-buy-tie-zulu"]
    assert coverage["complete-buy-old-skito-copy"] == "secondary_view"
    assert coverage["complete-conflict-a"] == "quarantined"
    assert coverage["complete-conflict-b"] == "quarantined"
    assert coverage["complete-instruction-balance-conflict"] == "quarantined"
    assert coverage["complete-missing-token-metadata"] == "quarantined"

    movements = _load_json(GROUND_TRUTH_DIR / "lot_movements.json")["lot_movements"]
    mystery_event_id = event_by_record["complete-transfer-mystery-out"].event_id
    assert any(
        item["event_id"] == mystery_event_id and item["basis_state"] == "unknown"
        for item in movements
    )

    with (GROUND_TRUTH_DIR / "expected_disposals.csv").open(
        encoding="utf-8", newline=""
    ) as source_file:
        disposals = list(csv.DictReader(source_file))
    exact_event_id = event_by_record["complete-sale-01"].event_id
    after_event_id = event_by_record["complete-sale-after-boundary"].event_id
    assert {row["term"] for row in disposals if row["event_id"] == exact_event_id} == {"short"}
    assert {row["term"] for row in disposals if row["event_id"] == after_event_id} == {
        "long",
        "short",
    }

    mixed_event = event_by_record["complete-sale-mixed-mystery"]
    mixed_rows = [row for row in disposals if row["event_id"] == mixed_event.event_id]
    exceptions = _load_json(GROUND_TRUTH_DIR / "expected_exceptions.json")["exceptions"]
    mixed_exceptions = [
        item
        for item in exceptions
        if item["code"] == "unknown_basis_disposal_slice"
        and mixed_event.event_id in item["related_object_ids"]
    ]
    assert len(mixed_rows) == 1
    assert mixed_rows[0]["quantity_base_units"] == "1000000"
    assert (
        sum(
            int(fact["value"])
            for item in mixed_exceptions
            for fact in item["observed_facts"]
            if fact["name"] == "quantity_base_units"
        )
        == 1_500_000
    )

def test_complete_lots_consumptions_movements_and_reports_match_ground_truth(
    tmp_path: Path,
) -> None:
    engine = WalletTaxEngine(
        policy=US_FIFO_POLICY,
        ledger_class=CapturingLedger,
    )
    result = engine.run(fixtures_dir=FIXTURE_DIR, output_dir=tmp_path)
    ledger = CapturingLedger.latest
    assert ledger is not None

    actual_lots = [_lot_record(lot) for lot in ledger.all_lots()]
    actual_consumptions = [
        {
            "consumption_id": item.consumption_id,
            "disposal_event_id": item.disposal_event_id,
            "lot_id": item.lot_id,
            "quantity_base_units": item.quantity_base_units,
            "basis_state": item.consumed_basis.state.value,
            "consumed_basis_usd": _money(item.consumed_basis),
            "basis_exception_id": (
                None if item.consumed_basis.is_known else item.consumed_basis.require_exception_id()
            ),
        }
        for item in ledger.all_consumptions()
    ]
    actual_movements = [
        {
            "movement_id": item.movement_id,
            "event_id": item.event_id,
            "source_lot_id": item.source_lot_id,
            "destination_lot_id": item.destination_lot_id,
            "source_wallet_id": item.source_wallet_id,
            "destination_wallet_id": item.destination_wallet_id,
            "mint": item.mint,
            "quantity_base_units": item.quantity_base_units,
            "basis_state": item.moved_basis.state.value,
            "moved_basis_usd": _money(item.moved_basis),
            "destination_state": item.destination_state.value,
        }
        for item in ledger.all_movements()
    ]
    actual_ending = [
        _lot_record(lot) for lot in ledger.all_lots() if lot.remaining_quantity_base_units > 0
    ]

    assert actual_lots == _load_json(GROUND_TRUTH_DIR / "lots.json")["lots"]
    assert actual_consumptions == _load_json(GROUND_TRUTH_DIR / "consumptions.json")["consumptions"]
    assert actual_movements == _load_json(GROUND_TRUTH_DIR / "lot_movements.json")["lot_movements"]
    assert (
        actual_ending == _load_json(GROUND_TRUTH_DIR / "ending_inventory.json")["ending_inventory"]
    )
    assert result.reconciliation_passed
    assert (result.disposal_count, result.income_count, result.exception_count) == (
        35,
        7,
        24,
    )
    for actual_name, expected_name in (
        ("disposals.csv", "expected_disposals.csv"),
        ("income.csv", "expected_income.csv"),
        ("exceptions.json", "expected_exceptions.json"),
    ):
        assert (tmp_path / actual_name).read_bytes() == (
            GROUND_TRUTH_DIR / expected_name
        ).read_bytes()

@pytest.mark.parametrize(
    "relative_path",
    (
        "ownership.json",
        "tokens.json",
        "prices.json",
        "scenario-catalog.json",
        "wallets/wallet_alpha.json",
        "wallets/wallet_beta.json",
    ),
)
def test_complete_fixture_contains_only_obviously_synthetic_identity_text(
    relative_path: str,
) -> None:
    text = (FIXTURE_DIR / relative_path).read_text(encoding="utf-8")

    assert "synthetic_" in text or "final_wallet_history" in text
    assert not re.search(r"(?<![A-Za-z0-9_])[1-9A-HJ-NP-Za-km-z]{32,44}(?![A-Za-z0-9_])", text)
    assert not re.search(r"\b(?:mainnet|real_client|private_key|seed_phrase)\b", text, re.I)
