"""Adapt complete accounting results into deterministic CSV and JSON outputs."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from engine.models import (
    DisposalRow,
    DomainException,
    IncomeRow,
)
from engine.policy.config import (
    CONTRACT_VERSION,
    AssessmentPolicy,
    RoundingRule,
)

DISPOSAL_COLUMNS = (
    "contract_version",
    "fixture_set_id",
    "policy_id",
    "policy_version",
    "valuation_currency",
    "disposal_id",
    "event_id",
    "source_id",
    "lot_id",
    "consumption_id",
    "wallet_id",
    "mint",
    "symbol",
    "quantity_base_units",
    "quantity",
    "date_acquired",
    "date_disposed",
    "proceeds_usd",
    "cost_basis_usd",
    "gain_loss_usd",
    "term",
)
INCOME_COLUMNS = (
    "contract_version",
    "fixture_set_id",
    "policy_id",
    "policy_version",
    "valuation_currency",
    "income_id",
    "event_id",
    "source_id",
    "wallet_id",
    "mint",
    "symbol",
    "category",
    "quantity_base_units",
    "quantity",
    "received_at",
    "price_usd",
    "price_source_id",
    "fair_value_usd",
    "created_lot_id",
)

def _money(value: Decimal, *, policy: AssessmentPolicy) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"Report money must be a finite Decimal, got {value!r}.")
    if policy.rounding_rule is not RoundingRule.HALF_EVEN:
        raise ValueError(f"Unsupported report rounding: {policy.rounding_rule.value}.")
    quantum = Decimal(1).scaleb(-policy.report_decimal_places)
    return format(value.quantize(quantum, rounding=ROUND_HALF_EVEN), "f")

def _quantity(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"Report quantity must be a finite Decimal, got {value!r}.")
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text

def _disposal_record(
    row: DisposalRow, *, fixture_set_id: str, policy: AssessmentPolicy
) -> dict[str, str | int]:
    return {
        "contract_version": CONTRACT_VERSION,
        "fixture_set_id": fixture_set_id,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "valuation_currency": policy.valuation_currency.value,
        "disposal_id": row.disposal_id,
        "event_id": row.event_id,
        "source_id": row.source_id,
        "lot_id": row.lot_id,
        "consumption_id": row.consumption_id,
        "wallet_id": row.wallet_id,
        "mint": row.mint,
        "symbol": row.symbol,
        "quantity_base_units": row.quantity_base_units,
        "quantity": _quantity(row.quantity),
        "date_acquired": row.date_acquired.date().isoformat(),
        "date_disposed": row.date_disposed.date().isoformat(),
        "proceeds_usd": _money(row.proceeds_usd, policy=policy),
        "cost_basis_usd": _money(row.cost_basis_usd, policy=policy),
        "gain_loss_usd": _money(row.gain_loss_usd, policy=policy),
        "term": row.term.value,
    }

def _income_record(
    row: IncomeRow, *, fixture_set_id: str, policy: AssessmentPolicy
) -> dict[str, str | int]:
    return {
        "contract_version": CONTRACT_VERSION,
        "fixture_set_id": fixture_set_id,
        "policy_id": policy.policy_id,
        "policy_version": policy.policy_version,
        "valuation_currency": policy.valuation_currency.value,
        "income_id": row.income_id,
        "event_id": row.event_id,
        "source_id": row.source_id,
        "wallet_id": row.wallet_id,
        "mint": row.mint,
        "symbol": row.symbol,
        "category": row.category,
        "quantity_base_units": row.quantity_base_units,
        "quantity": _quantity(row.quantity),
        "received_at": row.received_at.isoformat().replace("+00:00", "Z"),
        "price_usd": _money(row.price_usd, policy=policy),
        "price_source_id": row.price_source_id,
        "fair_value_usd": _money(row.fair_value_usd, policy=policy),
        "created_lot_id": row.created_lot_id,
    }

def _write_csv(
    path: Path, *, columns: tuple[str, ...], rows: list[dict[str, str | int]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

def write_reports(
    *,
    output_dir: Path,
    fixture_set_id: str,
    policy: AssessmentPolicy,
    disposals: tuple[DisposalRow, ...],
    income: tuple[IncomeRow, ...],
    exceptions: tuple[DomainException, ...],
) -> None:
    """Write all mandatory outputs with stable columns, rows, and JSON keys."""
    output_dir.mkdir(parents=True, exist_ok=True)
    disposal_records = [
        _disposal_record(row, fixture_set_id=fixture_set_id, policy=policy)
        for row in sorted(disposals, key=lambda item: item.disposal_id)
    ]
    income_records = [
        _income_record(row, fixture_set_id=fixture_set_id, policy=policy)
        for row in sorted(income, key=lambda item: item.income_id)
    ]
    _write_csv(output_dir / "disposals.csv", columns=DISPOSAL_COLUMNS, rows=disposal_records)
    _write_csv(output_dir / "income.csv", columns=INCOME_COLUMNS, rows=income_records)

    exception_records = []
    for item in sorted(exceptions, key=lambda exception: exception.exception_id):
        record = asdict(item)
        record["severity"] = item.severity.value
        record["stage"] = item.stage.value
        exception_records.append(record)
    payload = {
        "contract_version": CONTRACT_VERSION,
        "fixture_set_id": fixture_set_id,
        **policy.metadata(),
        "exceptions": exception_records,
    }
    (output_dir / "exceptions.json").write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
