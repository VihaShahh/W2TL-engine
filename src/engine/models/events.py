"""Canonical event, classification, and source-coverage contracts.

The dataclasses are frozen because they represent facts passed between pipeline stages. A later
stage must derive a new result instead of rewriting the evidence that an earlier stage observed.
Slots keep the field contract strict and prevent accidental misspelled attributes.
"""

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
    """One mint, direction, and exact quantity component of a normalized event.

    Legs are separate objects because swaps and fee-bearing events can move several assets. They
    describe movement only; basis, gain, and tax classification belong to later stages.
    """

    component_id: str
    mint: str
    symbol: str
    decimals: int
    direction: Direction
    quantity_base_units: int

@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """Canonical source-linked facts consumed by classification and accounting.

    The model unifies source shapes while deliberately excluding lots and report fields. This
    keeps normalization from deciding a tax result while it is still describing evidence.
    """

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
    """Evidence-backed economic label and resolution state for one logical event.

    Keeping this separate from the event preserves the observed facts and records which stable
    rule and source records supported the later interpretation.
    """

    event_id: str
    type: ClassificationType
    state: ResolutionState
    rule_id: str
    evidence_source_ids: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class CoverageEntry:
    """Final accounting of how one stored source record was handled.

    Coverage includes secondary and quarantined records so a plausible report cannot hide an
    input that disappeared before accounting.
    """

    source: SourceReference
    status: CoverageStatus
    event_id: str | None
    exception_ids: tuple[str, ...]
