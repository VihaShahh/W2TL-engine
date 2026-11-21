"""Ledger and localization extension-boundary checks."""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from engine.accounting import FifoLedger
from engine.country_US import UsTaxReportHandler
from engine.evidence.loader import load_fixture
from engine.evidence.processor import process_evidence
from engine.interfaces.cli import main
from engine.interfaces.explain import ExplainRenderer
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
    assert "WALLET TAX ENGINE · EXPLAIN" in output
    assert "01/04 · LOAD + VALIDATE" in output
    assert "02/04 · NORMALIZE + CLASSIFY" in output
    assert "03/04 · ACCOUNT" in output
    assert "04/04 · WRITE REPORTS" in output
    assert "✓ process_evidence()" in output
    assert "85 logical event(s)" in output
    assert "01/85  BUY · RESOLVED" in output
    assert "Accounting with FifoLedger (method=FIFO)" in output
    assert "UsTaxReportHandler.prepare_disposal_rows()" in output
    assert "basis=USD" in output
    assert "unknown basis" in output
    assert "disposals=35  ·  income=7  ·  exceptions=24" in output
    assert "RUN COMPLETE" in output
    assert "35 disposal rows  ·  7 income rows  ·  24 exceptions" in output
    assert "Reconciliation  PASSED" in output
    assert "\033[" not in output
    assert (tmp_path / "disposals.csv").is_file()


def test_explain_renderer_highlights_each_phase_in_a_terminal() -> None:
    stream = StringIO()
    renderer = ExplainRenderer(
        fixtures_dir=Path("fixtures/complete"),
        output_dir=Path("output/explain"),
        stream=stream,
        use_color=True,
    )

    for trace_message in (
        "[1/4] Loading and validating fixture evidence",
        "[2/4] Normalizing, matching, and classifying evidence",
        "[3/4] Accounting with FifoLedger",
        "[4/4] Writing deterministic reports",
    ):
        renderer.trace(trace_message)

    output = stream.getvalue()
    assert "\033[36;1m" in output
    assert "\033[34;1m" in output
    assert "\033[35;1m" in output
    assert "\033[32;1m" in output
