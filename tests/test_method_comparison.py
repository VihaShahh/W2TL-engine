"""Isolated deterministic FIFO, HIFO, and specific-ID comparison outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from engine.interfaces.cli import main
from engine.policy.config import US_FIFO_POLICY
from engine.reporting.comparison import write_method_comparison

FIXTURE = Path("fixtures/bonus/alternative-allocation.json")

def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

def test_method_comparison_writes_isolated_reconciled_outputs(tmp_path: Path) -> None:
    comparison_path = write_method_comparison(
        fixture_path=FIXTURE,
        output_dir=tmp_path,
        policy=US_FIFO_POLICY,
    )
    comparison = _read(comparison_path)
    methods = {
        item["allocation_method"]: item for item in comparison["methods"]
    }

    assert set(methods) == {"FIFO", "HIFO", "SPECIFIC_IDENTIFICATION"}
    assert {item["quantity_base_units"] for item in methods.values()} == {2_000_000}
    assert {item["proceeds_usd"] for item in methods.values()} == {"14.00"}
    assert methods["FIFO"]["cost_basis_usd"] == "4.00"
    assert methods["FIFO"]["gain_loss_usd"] == "10.00"
    assert methods["FIFO"]["terms"] == ["long"]
    assert methods["HIFO"]["cost_basis_usd"] == "10.00"
    assert methods["HIFO"]["gain_loss_usd"] == "4.00"
    assert methods["HIFO"]["terms"] == ["short"]
    assert methods["SPECIFIC_IDENTIFICATION"]["cost_basis_usd"] == "7.00"
    assert methods["SPECIFIC_IDENTIFICATION"]["gain_loss_usd"] == "7.00"
    assert methods["SPECIFIC_IDENTIFICATION"]["terms"] == ["long", "short"]
    assert methods["HIFO"]["difference_from_fifo"] == {
        "cost_basis_usd": "6.00",
        "gain_loss_usd": "-6.00",
        "selected_lots_changed": True,
    }
    for directory, method in (
        ("fifo", "FIFO"),
        ("hifo", "HIFO"),
        ("specific_identification", "SPECIFIC_IDENTIFICATION"),
    ):
        result = _read(tmp_path / directory / "result.json")
        assert result["allocation_method"] == method
        assert result["reconciliation"]["status"] == "passed"
        assert result["assessment_policy_id"] == US_FIFO_POLICY.policy_id

def test_method_comparison_command_is_byte_stable(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    args = ["compare-methods", "--fixture", str(FIXTURE)]

    assert main([*args, "--output", str(first)]) == 0
    assert main([*args, "--output", str(second)]) == 0

    first_files = sorted(path for path in first.rglob("*") if path.is_file())
    second_files = sorted(path for path in second.rglob("*") if path.is_file())
    assert [path.relative_to(first) for path in first_files] == [
        path.relative_to(second) for path in second_files
    ]
    assert [path.read_bytes() for path in first_files] == [
        path.read_bytes() for path in second_files
    ]
