"""Contract-version and frozen-model checks."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from engine.models import Acquisition, Basis, EvidenceState, TokenMetadata
from engine.policy.config import (
    CONTRACT_VERSION,
    UnsupportedContractError,
    require_supported_contract,
)


def test_supported_contract_is_accepted() -> None:
    require_supported_contract(actual=CONTRACT_VERSION, name="fixture set")

@pytest.mark.parametrize("actual", [None, 0, "1"])
def test_unsupported_contract_is_rejected(actual: object) -> None:
    with pytest.raises(UnsupportedContractError, match="Unsupported fixture set contract"):
        require_supported_contract(actual=actual, name="fixture set")

def test_domain_contracts_are_immutable() -> None:
    token = TokenMetadata(mint="synthetic_mint_skito", symbol="SKITO", decimals=6)

    with pytest.raises(FrozenInstanceError):
        token.decimals = 9  # type: ignore[misc]

def test_basis_factories_create_valid_known_and_unknown_states() -> None:
    known = Basis.known(Decimal("12.50"))
    unknown = Basis.unknown("exc_missing_basis")

    assert known.state is EvidenceState.KNOWN
    assert known.require_total_usd() == Decimal("12.50")
    assert unknown.state is EvidenceState.UNKNOWN
    assert unknown.require_exception_id() == "exc_missing_basis"
    with pytest.raises(ValueError, match="no supported monetary total"):
        unknown.require_total_usd()

def test_basis_rejects_contradictory_state_fields() -> None:
    with pytest.raises(ValueError, match="Known basis requires"):
        Basis(state=EvidenceState.KNOWN)
    with pytest.raises(ValueError, match="Unknown basis requires"):
        Basis(state=EvidenceState.UNKNOWN, total_usd=Decimal("1"))
    with pytest.raises(ValueError, match="Unknown basis requires"):
        Basis(state=EvidenceState.UNKNOWN, exception_id="")

def test_acquisition_factories_create_valid_known_and_unknown_states() -> None:
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    known = Acquisition.known(timestamp)
    unknown = Acquisition.unknown(timestamp, "exc_missing_date")

    assert known.require_timestamp_utc() == timestamp
    assert known.sort_timestamp_utc == timestamp
    assert unknown.sort_timestamp_utc == timestamp
    assert unknown.require_exception_id() == "exc_missing_date"
    with pytest.raises(ValueError, match="no supported acquisition timestamp"):
        unknown.require_timestamp_utc()

def test_acquisition_rejects_contradictory_state_fields() -> None:
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="Known acquisition requires"):
        Acquisition(state=EvidenceState.KNOWN, observed_at_utc=timestamp)
    with pytest.raises(ValueError, match="Unknown acquisition requires"):
        Acquisition(state=EvidenceState.UNKNOWN, timestamp_utc=timestamp)
    with pytest.raises(ValueError, match="Unknown acquisition requires"):
        Acquisition(state=EvidenceState.UNKNOWN, observed_at_utc=timestamp)
