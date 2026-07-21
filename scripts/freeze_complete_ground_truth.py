"""Freeze reviewed complete-fixture results; never invoked by the test suite."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import ClassVar

from engine.accounting import FifoLedger
from engine.evidence.loader import load_fixture
from engine.evidence.processor import process_evidence
from engine.models import Basis, Lot
from engine.pipeline import WalletTaxEngine
from engine.policy.config import US_FIFO_POLICY

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "fixtures" / "complete"
GROUND_TRUTH_DIR = FIXTURE_DIR / "ground_truth"

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

def main() -> None:
    fixture = load_fixture(FIXTURE_DIR, policy=US_FIFO_POLICY)
    evidence = process_evidence(fixture)
    with tempfile.TemporaryDirectory(prefix="w2tl-ground-truth-") as temp_dir:
        output_dir = Path(temp_dir)
        WalletTaxEngine(
            policy=US_FIFO_POLICY,
            ledger_class=CapturingLedger,
        ).run(fixtures_dir=FIXTURE_DIR, output_dir=output_dir)
        ledger = CapturingLedger.latest
        if ledger is None:
            raise AssertionError("The complete fixture did not create a ledger.")

        classifications = [
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
        (GROUND_TRUTH_DIR / "classifications.json").write_text(
            json.dumps(
                {
                    "fixture_set_id": fixture.fixture_set_id,
                    "classifications": classifications,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        lots = ledger.all_lots()
        (GROUND_TRUTH_DIR / "lots.json").write_text(
            json.dumps(
                {
                    "fixture_set_id": fixture.fixture_set_id,
                    "lots": [_lot_record(lot) for lot in lots],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (GROUND_TRUTH_DIR / "consumptions.json").write_text(
            json.dumps(
                {
                    "fixture_set_id": fixture.fixture_set_id,
                    "consumptions": [
                        {
                            "consumption_id": item.consumption_id,
                            "disposal_event_id": item.disposal_event_id,
                            "lot_id": item.lot_id,
                            "quantity_base_units": item.quantity_base_units,
                            "basis_state": item.consumed_basis.state.value,
                            "consumed_basis_usd": _money(item.consumed_basis),
                            "basis_exception_id": (
                                None
                                if item.consumed_basis.is_known
                                else item.consumed_basis.require_exception_id()
                            ),
                        }
                        for item in ledger.all_consumptions()
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (GROUND_TRUTH_DIR / "lot_movements.json").write_text(
            json.dumps(
                {
                    "fixture_set_id": fixture.fixture_set_id,
                    "lot_movements": [
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
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        ending_lots = tuple(lot for lot in lots if lot.remaining_quantity_base_units > 0)
        (GROUND_TRUTH_DIR / "ending_inventory.json").write_text(
            json.dumps(
                {
                    "fixture_set_id": fixture.fixture_set_id,
                    "ending_inventory": [_lot_record(lot) for lot in ending_lots],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        known_basis = sum(
            lot.remaining_basis.require_total_usd()
            for lot in ending_lots
            if lot.remaining_basis.is_known
        )
        unknown_quantity = sum(
            lot.remaining_quantity_base_units
            for lot in ending_lots
            if not lot.remaining_basis.is_known
        )
        reconciliation = {
            "stored_record_count": len(fixture.all_source_refs),
            "valid_transaction_count": len(fixture.transactions),
            "logical_event_count": len(evidence.events),
            "coverage_counts": dict(
                sorted(Counter(item.status.value for item in evidence.coverage).items())
            ),
            "classification_counts": dict(
                sorted(Counter(item.type.value for item in evidence.classifications).items())
            ),
            "lot_count": len(lots),
            "consumption_count": len(ledger.all_consumptions()),
            "movement_count": len(ledger.all_movements()),
            "ending_lot_count": len(ending_lots),
            "ending_quantity_base_units": sum(
                lot.remaining_quantity_base_units for lot in ending_lots
            ),
            "ending_known_basis_usd": format(known_basis, "f"),
            "ending_unknown_basis_quantity_base_units": unknown_quantity,
            "disposal_row_count": sum(
                1 for _ in (output_dir / "disposals.csv").read_text().splitlines()[1:]
            ),
            "income_row_count": sum(
                1 for _ in (output_dir / "income.csv").read_text().splitlines()[1:]
            ),
            "exception_count": len(
                json.loads((output_dir / "exceptions.json").read_text(encoding="utf-8"))[
                    "exceptions"
                ]
            ),
            "review_status": "hand_calculations_documented_in_fixture_readme",
        }
        (GROUND_TRUTH_DIR / "reconciliation.json").write_text(
            json.dumps(
                {
                    "fixture_set_id": fixture.fixture_set_id,
                    "reconciliation": reconciliation,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        shutil.copyfile(output_dir / "disposals.csv", GROUND_TRUTH_DIR / "expected_disposals.csv")
        shutil.copyfile(output_dir / "income.csv", GROUND_TRUTH_DIR / "expected_income.csv")
        shutil.copyfile(
            output_dir / "exceptions.json",
            GROUND_TRUTH_DIR / "expected_exceptions.json",
        )

if __name__ == "__main__":
    main()
