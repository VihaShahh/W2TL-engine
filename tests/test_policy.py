"""Versioned assessment-policy selection and output metadata checks."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from engine.interfaces.cli import main
from engine.pipeline import run_pipeline
from engine.policy.config import (
    US_FIFO_POLICY,
    AssessmentPolicy,
    BasisMethod,
    BasisScope,
    FeeRule,
    HoldingTermRule,
    Jurisdiction,
    OutboundRule,
    PartialOutputRule,
    PriceRule,
    RoundingRule,
    SpamRule,
    SwapValuationRule,
    UnsupportedPolicyError,
    ValuationCurrency,
    require_supported_policy,
    select_policy,
)


def test_us_policy_contains_every_required_selection() -> None:
    assert AssessmentPolicy(
        policy_id="us_federal_assessment_fifo",
        policy_version=1,
        jurisdiction=Jurisdiction.US,
        valuation_currency=ValuationCurrency.USD,
        basis_method=BasisMethod.FIFO,
        basis_scope=BasisScope.WALLET_AND_MINT,
        price_rule=PriceRule.EXACT_TIMESTAMP,
        swap_valuation_rule=SwapValuationRule.REQUIRE_AGREEING_LEGS,
        holding_term_rule=HoldingTermRule.MORE_THAN_ONE_YEAR,
        partial_output_rule=PartialOutputRule.REPORT_COMPLETE_SLICES,
        outbound_rule=OutboundRule.SUSPENSE,
        spam_rule=SpamRule.QUARANTINE_UNPRICED,
        fee_rule=FeeRule.EVENT_CONTEXT,
        rounding_rule=RoundingRule.HALF_EVEN,
        report_decimal_places=2,
    ) == US_FIFO_POLICY

def test_policy_is_immutable_and_rejects_invalid_version() -> None:
    with pytest.raises(FrozenInstanceError):
        US_FIFO_POLICY.policy_version = 2  # type: ignore[misc]
    with pytest.raises(ValueError, match="positive integer"):
        replace(US_FIFO_POLICY, policy_version=0)
    with pytest.raises(ValueError, match="supported selection"):
        replace(US_FIFO_POLICY, basis_method="FIFO")  # type: ignore[arg-type]

def test_policy_selection_has_no_id_version_or_definition_fallback() -> None:
    assert (
        select_policy(
            policy_id=US_FIFO_POLICY.policy_id,
            policy_version=US_FIFO_POLICY.policy_version,
        )
        is US_FIFO_POLICY
    )
    with pytest.raises(UnsupportedPolicyError, match="version 2"):
        select_policy(policy_id=US_FIFO_POLICY.policy_id, policy_version=2)
    with pytest.raises(UnsupportedPolicyError, match="other_country"):
        select_policy(policy_id="other_country", policy_version=1)
    with pytest.raises(UnsupportedPolicyError, match="version True"):
        select_policy(policy_id=US_FIFO_POLICY.policy_id, policy_version=True)
    with pytest.raises(UnsupportedPolicyError, match="definition"):
        require_supported_policy(replace(US_FIFO_POLICY, report_decimal_places=3))

def test_generated_metadata_records_the_complete_selected_policy(tmp_path: Path) -> None:
    run_pipeline(
        fixtures_dir=Path("fixtures/complete"),
        output_dir=tmp_path,
        policy=US_FIFO_POLICY,
    )

    payload = json.loads((tmp_path / "exceptions.json").read_text(encoding="utf-8"))
    for name, value in US_FIFO_POLICY.metadata().items():
        assert payload[name] == value

def test_cli_rejects_unsupported_policy_before_creating_output(
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
            "--policy-version",
            "2",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert "version 2" in captured.err
    assert not output_dir.exists()
