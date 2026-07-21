"""Focused checks for strict fixture loading and evidence preservation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from engine.core.values import assert_no_binary_floats
from engine.evidence.loader import FixtureValidationError, load_fixture
from engine.policy.config import US_FIFO_POLICY, UnsupportedContractError

FIXTURE_DIR = Path("fixtures/complete")
COMPLETE_FIXTURE_DIR = Path("fixtures/complete")

def _copy_fixture(source: Path, destination: Path) -> Path:
    shutil.copytree(source, destination)
    return destination

def test_loader_returns_stable_exact_evidence() -> None:
    fixture = load_fixture(FIXTURE_DIR, policy=US_FIFO_POLICY)

    assert fixture.fixture_set_id == "final_wallet_history"
    assert fixture.owned_wallet_ids == ("synthetic_wallet_alpha", "synthetic_wallet_beta")
    assert len(fixture.all_source_refs) == 96
    assert len(fixture.transactions) == 96
    assert_no_binary_floats(fixture)

def test_loader_rejects_unsupported_manifest_version(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        '{"contract_version":0}\n', encoding="utf-8"
    )

    with pytest.raises(UnsupportedContractError, match="fixture set"):
        load_fixture(tmp_path, policy=US_FIFO_POLICY)

def test_loader_rejects_json_float_before_contract_parsing(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(
        '{"contract_version":1.5}\n', encoding="utf-8"
    )

    with pytest.raises(FixtureValidationError, match="binary-float"):
        load_fixture(tmp_path, policy=US_FIFO_POLICY)

def test_loader_keeps_all_finalized_records_before_evidence_review() -> None:
    fixture = load_fixture(COMPLETE_FIXTURE_DIR, policy=US_FIFO_POLICY)

    assert len(fixture.all_source_refs) == 96
    assert len(fixture.transactions) == 96
    assert fixture.load_exceptions == ()

def test_loader_treats_missing_required_file_as_fatal(tmp_path: Path) -> None:
    fixture_dir = _copy_fixture(FIXTURE_DIR, tmp_path / "fixture")
    (fixture_dir / "prices.json").unlink()

    with pytest.raises(FixtureValidationError, match="Required fixture file is missing"):
        load_fixture(fixture_dir, policy=US_FIFO_POLICY)

def test_loader_rejects_ground_truth_that_contradicts_run_policy(tmp_path: Path) -> None:
    fixture_dir = _copy_fixture(FIXTURE_DIR, tmp_path / "fixture")
    metadata_path = fixture_dir / "ground_truth" / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["valuation_currency"] = "EUR"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(FixtureValidationError, match="policy metadata differs"):
        load_fixture(fixture_dir, policy=US_FIFO_POLICY)

def test_loader_rejects_real_looking_token_identity(tmp_path: Path) -> None:
    fixture_dir = _copy_fixture(FIXTURE_DIR, tmp_path / "fixture")
    tokens_path = fixture_dir / "tokens.json"
    tokens = json.loads(tokens_path.read_text(encoding="utf-8"))
    tokens["tokens"][0]["mint"] = "realLookingSolanaAddress"
    tokens_path.write_text(json.dumps(tokens), encoding="utf-8")

    with pytest.raises(FixtureValidationError, match="not visibly synthetic"):
        load_fixture(fixture_dir, policy=US_FIFO_POLICY)
