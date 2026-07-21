"""CLI health reporting for fatal failures and expected business exceptions."""

from __future__ import annotations

from pathlib import Path
from typing import NoReturn

import pytest

from engine.accounting import ReconciliationError
from engine.interfaces.cli import main


def test_fatal_fixture_validation_returns_nonzero_without_success_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_dir = tmp_path / "output"

    exit_code = main(
        [
            "run",
            "--fixtures",
            str(tmp_path / "missing-fixture"),
            "--output",
            str(output_dir),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert "Run failed: Required fixture file is missing:" in captured.err
    assert not output_dir.exists()

def test_fatal_reconciliation_returns_nonzero_without_success_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_reconciliation(**kwargs: object) -> NoReturn:
        raise ReconciliationError("synthetic quantity invariant failed")

    monkeypatch.setattr("engine.pipeline.run_pipeline", fail_reconciliation)

    exit_code = main(
        [
            "run",
            "--fixtures",
            "fixtures/complete",
            "--output",
            str(tmp_path / "output"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == "Run failed: synthetic quantity invariant failed\n"

def test_expected_business_exceptions_keep_success_and_disclose_run_health(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_dir = tmp_path / "output"

    exit_code = main(
        [
            "run",
            "--fixtures",
            "fixtures/complete",
            "--output",
            str(output_dir),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "35 disposal row(s), 7 income row(s), and 24 exception(s)" in captured.out
    assert "Reconciliation passed." in captured.out
    assert captured.err == ""
    assert (output_dir / "exceptions.json").is_file()
