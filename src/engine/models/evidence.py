"""Immutable source, fixture, token, and price evidence contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from engine.models.review import DomainException
from engine.models.states import PriceResolutionState, TransactionStatus


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Stable pointer from a derived object back to one source record."""

    source_id: str
    fixture_set_id: str
    wallet_id: str
    fixture_record_id: str
    signature: str

@dataclass(frozen=True, slots=True)
class RawInstructionEvidence:
    """Synthetic instruction facts retained without assigning tax treatment."""

    program: str
    instruction_type: str
    mint: str
    quantity_base_units: int
    cash_value_usd: Decimal | None
    counterparty_wallet_id: str | None

@dataclass(frozen=True, slots=True)
class RawTokenBalanceChange:
    mint: str
    pre_balance_base_units: int
    post_balance_base_units: int
    delta_base_units: int

@dataclass(frozen=True, slots=True)
class FeeEvidence:
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
    """Validated source record whose values remain unchanged after loading."""

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
    mint: str
    symbol: str
    decimals: int

@dataclass(frozen=True, slots=True)
class PricePoint:
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
    """Exact lookup result with source matches retained for review."""

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
    scenario_id: str
    source_record_ids: tuple[str, ...]
    purpose: str
    naive_failure: str
    expected_outcome: str

@dataclass(frozen=True, slots=True)
class GroundTruthMetadata:
    contract_version: int
    fixture_set_id: str
    policy_id: str
    policy_version: int
    valuation_currency: str

@dataclass(frozen=True, slots=True)
class FixtureBundle:
    """Typed local evidence used by normalization and later accounting stages."""

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
