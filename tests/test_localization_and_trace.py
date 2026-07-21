"""Ledger and localization extension-boundary checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.accounting import FifoLedger
from engine.country_US import UsTaxReportHandler
from engine.evidence.loader import load_fixture
from engine.evidence.processor import process_evidence
from engine.interfaces.cli import main
from engine.models import ClassificationType
from engine.policy.config import US_FIFO_POLICY
from engine.reporting.handler import TaxReportHandler


def test_us_report_handler_prepares_rows_from_neutral_consumptions() -> None:
    evidence = process_evidence(
        load_fixture(Path("fixtures/complete"), policy=US_FIFO_POLICY)
    )
    buy = next(
        event
        for event, classification in zip(
            evidence.events, evidence.classifications, strict=True
        )
        if classification.type is ClassificationType.BUY
    )
    sale = next(
        event
        for event, classification in zip(
            evidence.events, evidence.classifications, strict=True
        )
        if classification.type is ClassificationType.SELL
    )
    ledger = FifoLedger()
    ledger.acquire(buy)
    consumptions = ledger.consume(sale)
    handler = UsTaxReportHandler(policy=US_FIFO_POLICY)

    rows = handler.prepare_disposal_rows(
        event=sale, consumptions=consumptions, ledger=ledger
    )

    assert isinstance(handler, TaxReportHandler)
    assert handler.localization == "country_US"
    assert len(rows) == 1
    assert rows[0].gain_loss_usd == 9

def test_explain_command_traces_the_real_core_pipeline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "explain",
            "--fixtures",
            "fixtures/complete",
            "--output",
            str(tmp_path),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "[1/4] Loading and validating fixture evidence" in output
    assert "process_evidence() -> 85 logical event(s)" in output
    assert "Accounting with FifoLedger (method=FIFO)" in output
    assert "UsTaxReportHandler.prepare_disposal_rows()" in output
    assert "basis=USD" in output
    assert "unknown basis" in output
    assert "disposals=35, income=7, exceptions=24" in output
    assert (tmp_path / "disposals.csv").is_file()
