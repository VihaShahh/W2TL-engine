"""Replay one versioned disposal fixture through isolated allocation methods."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import NoReturn, cast

from engine.accounting import (
    FifoLedger,
    HifoLedger,
    SpecificIdentificationLedger,
    TaxLotLedger,
    reconcile_run,
)
from engine.core.identity import derive_event_id, derive_source_id
from engine.core.values import parse_base_units, parse_decimal_string
from engine.country_US import UsTaxReportHandler
from engine.models import (
    Direction,
    DisposalRow,
    FeeEvidence,
    NormalizedEvent,
    SourceReference,
    SpecificIdentificationManifest,
    SpecificLotSelection,
    TokenLeg,
    TransactionStatus,
)
from engine.policy.config import (
    CONTRACT_VERSION,
    AssessmentPolicy,
    require_supported_contract,
    require_supported_policy,
)


class MethodComparisonFixtureError(ValueError):
    """Raised when the isolated alternative-allocation fixture is invalid."""

@dataclass(frozen=True, slots=True)
class _RequestedSelection:
    acquisition_record_id: str
    quantity_base_units: int

@dataclass(frozen=True, slots=True)
class _ComparisonFixture:
    fixture_set_id: str
    wallet_id: str
    mint: str
    acquisitions: tuple[tuple[str, NormalizedEvent], ...]
    disposal: NormalizedEvent
    selections: tuple[_RequestedSelection, ...]

@dataclass(frozen=True, slots=True)
class _MethodRun:
    method: str
    rows: tuple[DisposalRow, ...]
    payload: dict[str, object]

def _reject_float(value: str) -> NoReturn:
    raise MethodComparisonFixtureError(f"Binary-float input is forbidden: {value}.")

def _read_object(path: Path) -> dict[str, object]:
    try:
        raw: object = json.loads(
            path.read_text(encoding="utf-8"),
            parse_float=_reject_float,
            parse_constant=_reject_float,
        )
    except (FileNotFoundError, json.JSONDecodeError) as error:
        raise MethodComparisonFixtureError(f"Cannot read comparison fixture {path}.") from error
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        raise MethodComparisonFixtureError("Comparison fixture must be a JSON object.")
    return cast(dict[str, object], raw)

def _object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MethodComparisonFixtureError(f"{field} must be an object.")
    return cast(dict[str, object], value)

def _objects(value: object, *, field: str) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise MethodComparisonFixtureError(f"{field} must be an array.")
    return [_object(item, field=f"{field}[]") for item in value]

def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise MethodComparisonFixtureError(f"{field} must be a non-empty string.")
    return value

def _timestamp(value: object, *, field: str) -> datetime:
    text = _string(value, field=field)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise MethodComparisonFixtureError(f"{field} must be an ISO UTC timestamp.") from error
    if parsed.tzinfo != UTC:
        raise MethodComparisonFixtureError(f"{field} must use UTC.")
    return parsed

def _event(
    *,
    fixture_set_id: str,
    wallet_id: str,
    mint: str,
    symbol: str,
    decimals: int,
    record: dict[str, object],
    direction: Direction,
    slot: int,
    cash_field: str,
) -> tuple[str, NormalizedEvent]:
    record_id = _string(record.get("record_id"), field="record_id")
    signature = f"synthetic_signature_{record_id}"
    source_id = derive_source_id(
        fixture_set_id=fixture_set_id,
        wallet_id=wallet_id,
        fixture_record_id=record_id,
        signature=signature,
        slot=slot,
        instruction_index=0,
    )
    quantity = parse_base_units(
        record.get("quantity_base_units"), field_name="quantity_base_units"
    )
    if quantity <= 0:
        raise MethodComparisonFixtureError("Comparison quantities must be positive.")
    cash = parse_decimal_string(record.get(cash_field), field_name=cash_field)
    if cash < 0:
        raise MethodComparisonFixtureError("Comparison monetary values must not be negative.")
    event_id = derive_event_id(source_id=source_id)
    return record_id, NormalizedEvent(
        event_id=event_id,
        source_refs=(
            SourceReference(
                source_id=source_id,
                fixture_set_id=fixture_set_id,
                wallet_id=wallet_id,
                fixture_record_id=record_id,
                signature=signature,
            ),
        ),
        timestamp_utc=_timestamp(record.get("timestamp_utc"), field="timestamp_utc"),
        slot=slot,
        instruction_index=0,
        wallet_id=wallet_id,
        status=TransactionStatus.SUCCEEDED,
        program="synthetic_method_comparison",
        instruction_type="buy" if direction is Direction.INBOUND else "sell",
        token_legs=(
            TokenLeg(
                component_id=f"cmp_{record_id}",
                mint=mint,
                symbol=symbol,
                decimals=decimals,
                direction=direction,
                quantity_base_units=quantity,
            ),
        ),
        fee=FeeEvidence(wallet_id, 0, False),
        cash_value_usd=cash,
        counterparty_wallet_id=None,
    )

def _load_fixture(path: Path) -> _ComparisonFixture:
    raw = _read_object(path)
    try:
        require_supported_contract(
            actual=raw.get("contract_version"), name="method-comparison fixture"
        )
    except ValueError as error:
        raise MethodComparisonFixtureError(str(error)) from error
    fixture_set_id = _string(raw.get("fixture_set_id"), field="fixture_set_id")
    wallet_id = _string(raw.get("wallet_id"), field="wallet_id")
    mint = _string(raw.get("mint"), field="mint")
    symbol = _string(raw.get("symbol"), field="symbol")
    decimals = parse_base_units(raw.get("decimals"), field_name="decimals")
    acquisition_records = _objects(raw.get("acquisitions"), field="acquisitions")
    if len(acquisition_records) < 2:
        raise MethodComparisonFixtureError("Comparison requires at least two acquisitions.")
    acquisitions = tuple(
        _event(
            fixture_set_id=fixture_set_id,
            wallet_id=wallet_id,
            mint=mint,
            symbol=symbol,
            decimals=decimals,
            record=record,
            direction=Direction.INBOUND,
            slot=index + 1,
            cash_field="basis_usd",
        )
        for index, record in enumerate(acquisition_records)
    )
    record_ids = [record_id for record_id, _ in acquisitions]
    if len(record_ids) != len(set(record_ids)):
        raise MethodComparisonFixtureError("Acquisition record IDs must be unique.")
    _, disposal = _event(
        fixture_set_id=fixture_set_id,
        wallet_id=wallet_id,
        mint=mint,
        symbol=symbol,
        decimals=decimals,
        record=_object(raw.get("disposal"), field="disposal"),
        direction=Direction.OUTBOUND,
        slot=len(acquisitions) + 1,
        cash_field="proceeds_usd",
    )
    selections = tuple(
        _RequestedSelection(
            acquisition_record_id=_string(
                item.get("acquisition_record_id"), field="acquisition_record_id"
            ),
            quantity_base_units=parse_base_units(
                item.get("quantity_base_units"), field_name="quantity_base_units"
            ),
        )
        for item in _objects(raw.get("specific_identification"), field="specific_identification")
    )
    if not selections or any(item.quantity_base_units <= 0 for item in selections):
        raise MethodComparisonFixtureError("Specific identification requires positive selections.")
    if any(item.acquisition_record_id not in record_ids for item in selections):
        raise MethodComparisonFixtureError("Specific identification names an unknown acquisition.")
    return _ComparisonFixture(
        fixture_set_id=fixture_set_id,
        wallet_id=wallet_id,
        mint=mint,
        acquisitions=acquisitions,
        disposal=disposal,
        selections=selections,
    )

def _row_record(row: DisposalRow) -> dict[str, object]:
    return {
        "disposal_id": row.disposal_id,
        "event_id": row.event_id,
        "source_id": row.source_id,
        "lot_id": row.lot_id,
        "consumption_id": row.consumption_id,
        "wallet_id": row.wallet_id,
        "mint": row.mint,
        "symbol": row.symbol,
        "quantity_base_units": row.quantity_base_units,
        "date_acquired": row.date_acquired.isoformat().replace("+00:00", "Z"),
        "date_disposed": row.date_disposed.isoformat().replace("+00:00", "Z"),
        "proceeds_usd": format(row.proceeds_usd, "f"),
        "cost_basis_usd": format(row.cost_basis_usd, "f"),
        "gain_loss_usd": format(row.gain_loss_usd, "f"),
        "term": row.term.value,
    }

def _run_method(
    *,
    fixture: _ComparisonFixture,
    ledger: TaxLotLedger,
    policy: AssessmentPolicy,
) -> _MethodRun:
    lots_by_record = {
        record_id: ledger.acquire(event)
        for record_id, event in fixture.acquisitions
    }
    if isinstance(ledger, SpecificIdentificationLedger):
        manifest = SpecificIdentificationManifest(
            disposal_event_id=fixture.disposal.event_id,
            wallet_id=fixture.wallet_id,
            mint=fixture.mint,
            selections=tuple(
                SpecificLotSelection(
                    lot_id=lots_by_record[item.acquisition_record_id].lot_id,
                    quantity_base_units=item.quantity_base_units,
                )
                for item in fixture.selections
            ),
        )
        consumptions = ledger.consume_manifest(fixture.disposal, manifest=manifest)
    else:
        consumptions = ledger.consume(fixture.disposal)
    rows = UsTaxReportHandler(policy=policy).prepare_disposal_rows(
        event=fixture.disposal,
        consumptions=consumptions,
        ledger=ledger,
    )
    reconciliation = reconcile_run(ledger=ledger, disposals=rows, income=())
    payload: dict[str, object] = {
        "contract_version": CONTRACT_VERSION,
        "fixture_set_id": fixture.fixture_set_id,
        "assessment_policy_id": policy.policy_id,
        "assessment_policy_version": policy.policy_version,
        "allocation_method": ledger.method,
        "reconciliation": {
            "status": "passed",
            "lot_count": reconciliation.lot_count,
            "consumption_count": reconciliation.consumption_count,
            "disposal_count": reconciliation.disposal_count,
        },
        "slices": [_row_record(row) for row in rows],
    }
    return _MethodRun(method=ledger.method, rows=rows, payload=payload)

def _total(rows: tuple[DisposalRow, ...], field: str) -> Decimal:
    return sum((cast(Decimal, getattr(row, field)) for row in rows), start=Decimal(0))

def _money(value: Decimal, *, policy: AssessmentPolicy) -> str:
    quantum = Decimal(1).scaleb(-policy.report_decimal_places)
    return format(value.quantize(quantum, rounding=ROUND_HALF_EVEN), "f")

def _method_summary(
    run: _MethodRun, *, fifo: _MethodRun, policy: AssessmentPolicy
) -> dict[str, object]:
    quantity = sum(row.quantity_base_units for row in run.rows)
    proceeds = _total(run.rows, "proceeds_usd")
    basis = _total(run.rows, "cost_basis_usd")
    gain = _total(run.rows, "gain_loss_usd")
    fifo_basis = _total(fifo.rows, "cost_basis_usd")
    fifo_gain = _total(fifo.rows, "gain_loss_usd")
    return {
        "allocation_method": run.method,
        "quantity_base_units": quantity,
        "proceeds_usd": _money(proceeds, policy=policy),
        "cost_basis_usd": _money(basis, policy=policy),
        "gain_loss_usd": _money(gain, policy=policy),
        "selected_lot_ids": [row.lot_id for row in run.rows],
        "terms": [row.term.value for row in run.rows],
        "difference_from_fifo": {
            "selected_lots_changed": [row.lot_id for row in run.rows]
            != [row.lot_id for row in fifo.rows],
            "cost_basis_usd": _money(basis - fifo_basis, policy=policy),
            "gain_loss_usd": _money(gain - fifo_gain, policy=policy),
        },
        "slices": [_row_record(row) for row in run.rows],
    }

def write_method_comparison(
    *, fixture_path: Path, output_dir: Path, policy: AssessmentPolicy
) -> Path:
    """Write isolated deterministic results and one slice-level method comparison."""
    require_supported_policy(policy)
    fixture = _load_fixture(fixture_path)
    runs = (
        _run_method(fixture=fixture, ledger=FifoLedger(), policy=policy),
        _run_method(fixture=fixture, ledger=HifoLedger(), policy=policy),
        _run_method(
            fixture=fixture,
            ledger=SpecificIdentificationLedger(),
            policy=policy,
        ),
    )
    fifo = runs[0]
    expected_quantity = fixture.disposal.token_legs[0].quantity_base_units
    expected_proceeds = fixture.disposal.cash_value_usd
    if expected_proceeds is None:
        raise AssertionError("Validated comparison disposal has no proceeds.")
    for run in runs:
        if (
            sum(row.quantity_base_units for row in run.rows) != expected_quantity
            or _total(run.rows, "proceeds_usd") != expected_proceeds
        ):
            raise ValueError(f"{run.method} does not conserve disposal quantity and proceeds.")
        method_dir = output_dir / run.method.lower()
        method_dir.mkdir(parents=True, exist_ok=True)
        (method_dir / "result.json").write_text(
            json.dumps(run.payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    comparison = {
        "contract_version": CONTRACT_VERSION,
        "fixture_set_id": fixture.fixture_set_id,
        "assessment_policy_id": policy.policy_id,
        "assessment_policy_version": policy.policy_version,
        "source_disposal_event_id": fixture.disposal.event_id,
        "methods": [
            _method_summary(run, fifo=fifo, policy=policy) for run in runs
        ],
    }
    comparison_path = output_dir / "comparison.json"
    comparison_path.write_text(
        json.dumps(comparison, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return comparison_path
