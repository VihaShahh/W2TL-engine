"""Canonical event, classification, and source-coverage contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from engine.models.evidence import FeeEvidence, SourceReference
from engine.models.states import (
    ClassificationType,
    CoverageStatus,
    Direction,
    ResolutionState,
    TransactionStatus,
)


@dataclass(frozen=True, slots=True)
class TokenLeg:
    component_id: str
    mint: str
    symbol: str
    decimals: int
    direction: Direction
    quantity_base_units: int

@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """Canonical chain facts without tax-form or lot-accounting fields."""

    event_id: str
    source_refs: tuple[SourceReference, ...]
    timestamp_utc: datetime
    slot: int
    instruction_index: int
    wallet_id: str
    status: TransactionStatus
    program: str
    instruction_type: str
    token_legs: tuple[TokenLeg, ...]
    fee: FeeEvidence
    cash_value_usd: Decimal | None
    counterparty_wallet_id: str | None

@dataclass(frozen=True, slots=True)
class Classification:
    event_id: str
    type: ClassificationType
    state: ResolutionState
    rule_id: str
    evidence_source_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class CoverageEntry:
    source: SourceReference
    status: CoverageStatus
    event_id: str | None
    exception_ids: tuple[str, ...]
