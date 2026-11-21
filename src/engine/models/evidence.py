"""Immutable source, fixture, token, and price evidence contracts.

These models preserve what a source supplied and where it came from. They do not infer tax
treatment, which lets normalization and classification be reviewed independently of loading.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from engine.models.review import DomainException
from engine.models.states import PriceResolutionState, TransactionStatus


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Stable pointer from a derived object back to one source record.

    The compact reference carries enough identity to trace a lot, report row, or exception to
    the exact fixture record without copying the mutable source payload downstream.
    """

    source_id: str
    fixture_set_id: str
    wallet_id: str
    fixture_record_id: str
    signature: str

@dataclass(frozen=True, slots=True)
class RawInstructionEvidence:
    """Synthetic instruction facts retained without assigning tax treatment.

    An instruction label is only evidence. Balance direction, status, ownership, and other
    context must still support a classification before accounting mutates inventory.
    """

    program: str
    instruction_type: str
    mint: str
    quantity_base_units: int
    cash_value_usd: Decimal | None
    counterparty_wallet_id: str | None

@dataclass(frozen=True, slots=True)
class RawTokenBalanceChange:
    """Pre-balance, post-balance, and signed delta for one token mint.

    Retaining all three values lets the loader reject contradictory arithmetic rather than
    trusting whichever amount would make later accounting convenient.
    """

    mint: str
    pre_balance_base_units: int
    post_balance_base_units: int
    delta_base_units: int

@dataclass(frozen=True, slots=True)
class FeeEvidence:
    """Observed fee charge, payer, asset metadata, and exact base-unit quantity.

    Fee evidence is separate from event classification because a failed transaction can revert
    its intended legs while still charging a real network fee.
    """

    payer_wallet_id: str
    fee_base_units: int
    charged: bool
    mint: str | None = None
    symbol: str | None = None
    decimals: int | None = None

    def __post_init__(self) -> None:
        if (
            not self.payer_wallet_id
            or isinstance(self.fee_base_units, bool)
            or not isinstance(self.fee_base_units, int)
            or self.fee_base_units < 0
            or not isinstance(self.charged, bool)
        ):
            raise ValueError("Fee evidence has invalid payer, quantity, or charged state.")
        token_fields = (self.mint, self.symbol, self.decimals)
        if self.charged:
            if (
                self.fee_base_units <= 0
                or not self.mint
                or not self.symbol
                or isinstance(self.decimals, bool)
                or not isinstance(self.decimals, int)
                or self.decimals < 0
            ):
                raise ValueError(
                    "A charged fee requires positive quantity and complete token metadata."
                )
        elif self.fee_base_units != 0 or any(value is not None for value in token_fields):
            raise ValueError("An uncharged fee must have zero quantity and no token metadata.")

    def require_mint(self) -> str:
        if not self.charged or self.mint is None:
            raise ValueError("Uncharged fee evidence has no mint.")
        return self.mint

    def require_symbol(self) -> str:
        if not self.charged or self.symbol is None:
            raise ValueError("Uncharged fee evidence has no symbol.")
        return self.symbol

    def require_decimals(self) -> int:
        if not self.charged or self.decimals is None:
            raise ValueError("Uncharged fee evidence has no decimal precision.")
        return self.decimals

@dataclass(frozen=True, slots=True)
class Transaction:
    """One validated source record whose values remain unchanged after loading.

    It is not yet a logical economic event: duplicate handling or owned-wallet transfer matching
    may combine this record with another observation before classification.
    """

    source: SourceReference
    slot: int
    timestamp_utc: datetime
    status: TransactionStatus
    instruction_index: int
    fee: FeeEvidence
    instructions: tuple[RawInstructionEvidence, ...]
    token_balance_changes: tuple[RawTokenBalanceChange, ...]

@dataclass(frozen=True, slots=True)
class TokenMetadata:
    """Source-backed token identity, display symbol, and base-unit precision.

    Accounting keys by mint rather than symbol, while decimals provide the exact conversion from
    integer protocol units to a human-readable quantity.
    """

    mint: str
    symbol: str
    decimals: int

@dataclass(frozen=True, slots=True)
class PricePoint:
    """One positive exact price observation with mint, time, currency, and source identity.

    The point does not choose among alternatives. The resolver separately decides whether an
    exact query has one match, no match, or conflicting matches.
    """

    price_id: str
    mint: str
    timestamp_utc: datetime
    valuation_currency: str
    price_usd: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.price_usd, Decimal) or self.price_usd <= 0:
            raise ValueError("Price point must contain a positive Decimal price.")

@dataclass(frozen=True, slots=True)
class PriceResolution:
    """Exact lookup result with source matches retained for review.

    The tagged result makes found, missing, and conflict mutually explicit. Only a single found
    match can expose a numeric price to accounting.
    """

    state: PriceResolutionState
    mint: str
    timestamp_utc: datetime
    valuation_currency: str
    matches: tuple[PricePoint, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.state, PriceResolutionState):
            raise ValueError("Price resolution state must be a supported selection.")
        expected_count = {
            PriceResolutionState.FOUND: 1,
            PriceResolutionState.MISSING: 0,
        }.get(self.state)
        if expected_count is not None and len(self.matches) != expected_count:
            raise ValueError(
                f"{self.state.value.title()} price resolution requires "
                f"{expected_count} source match(es)."
            )
        if self.state is PriceResolutionState.CONFLICT and len(self.matches) < 2:
            raise ValueError("Conflict price resolution requires at least two source matches.")
        if any(
            point.mint != self.mint
            or point.timestamp_utc != self.timestamp_utc
            or point.valuation_currency != self.valuation_currency
            for point in self.matches
        ):
            raise ValueError("Price resolution contains a source outside its exact query.")

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(point.price_id for point in self.matches)

    def require_price_usd(self) -> Decimal:
        if self.state is not PriceResolutionState.FOUND:
            raise ValueError(f"{self.state.value.title()} price resolution has no dollar value.")
        return self.matches[0].price_usd

    def require_source_id(self) -> str:
        if self.state is not PriceResolutionState.FOUND:
            raise ValueError(f"{self.state.value.title()} price resolution has no single source.")
        return self.matches[0].price_id

@dataclass(frozen=True, slots=True)
class ScenarioDefinition:
    """Named adversarial fixture case with its purpose and expected outcome.

    Mapping every source record to a scenario proves fixture volume exercises understood failure
    modes instead of padding the data set with meaningless records.
    """

    scenario_id: str
    source_record_ids: tuple[str, ...]
    purpose: str
    naive_failure: str
    expected_outcome: str

@dataclass(frozen=True, slots=True)
class GroundTruthMetadata:
    """Contract, fixture, policy, and currency identity for reviewed expectations.

    Expected accounting is meaningful only under the same versioned assumptions as the run, so
    mismatched metadata must fail before comparison.
    """

    contract_version: int
    fixture_set_id: str
    policy_id: str
    policy_version: int
    valuation_currency: str

@dataclass(frozen=True, slots=True)
class FixtureBundle:
    """Complete typed evidence snapshot handed from loading to later stages.

    Grouping ownership, transactions, prices, token metadata, scenarios, and source references
    prevents downstream code from accidentally mixing values loaded from different snapshots.
    """

    fixture_set_id: str
    client_id: str
    owned_wallet_ids: tuple[str, ...]
    tokens: tuple[TokenMetadata, ...]
    prices: tuple[PricePoint, ...]
    scenarios: tuple[ScenarioDefinition, ...]
    ground_truth_metadata: GroundTruthMetadata
    transactions: tuple[Transaction, ...]
    all_source_refs: tuple[SourceReference, ...]
    load_exceptions: tuple[DomainException, ...]
