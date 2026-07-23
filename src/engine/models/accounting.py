"""Immutable basis, acquisition, inventory, and multi-leg accounting results.

The ledger replaces frozen values when inventory changes instead of letting unrelated services
edit shared objects in place. Known and unknown facts are modeled as valid states so missing
evidence cannot leak into arithmetic as a sentinel zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from engine.models.events import NormalizedEvent, TokenLeg
from engine.models.evidence import FeeEvidence
from engine.models.states import (
    ClassificationType,
    Direction,
    EvidenceState,
    FeeContext,
    InventoryState,
)


@dataclass(frozen=True, slots=True)
class Basis:
    """Exact total basis or an exception-backed unknown state.

    The tagged representation makes absence of evidence impossible to confuse with USD 0. Callers
    must prove the known state before obtaining a numeric amount.
    """

    state: EvidenceState
    total_usd: Decimal | None = None
    exception_id: str | None = None

    def __post_init__(self) -> None:
        if self.state is EvidenceState.KNOWN:
            if not isinstance(self.total_usd, Decimal) or self.exception_id is not None:
                raise ValueError("Known basis requires a Decimal total and no exception ID.")
            return
        if self.total_usd is not None or not self.exception_id:
            raise ValueError("Unknown basis requires an exception ID and no total.")

    @classmethod
    def known(cls, total_usd: Decimal) -> Basis:
        return cls(state=EvidenceState.KNOWN, total_usd=total_usd)

    @classmethod
    def unknown(cls, exception_id: str) -> Basis:
        return cls(state=EvidenceState.UNKNOWN, exception_id=exception_id)

    @property
    def is_known(self) -> bool:
        return self.state is EvidenceState.KNOWN

    def require_total_usd(self) -> Decimal:
        if not self.is_known or self.total_usd is None:
            raise ValueError("Unknown basis has no supported monetary total.")
        return self.total_usd

    def require_exception_id(self) -> str:
        if self.state is not EvidenceState.UNKNOWN or self.exception_id is None:
            raise ValueError("Known basis has no blocking exception ID.")
        return self.exception_id

@dataclass(frozen=True, slots=True)
class Acquisition:
    """Known original acquisition time or exception-backed observation evidence.

    Unknown original acquisition remains distinct from the time an external inbound was first
    observed, because using the observation time could silently change holding term.
    """

    state: EvidenceState
    timestamp_utc: datetime | None = None
    observed_at_utc: datetime | None = None
    exception_id: str | None = None

    def __post_init__(self) -> None:
        if self.state is EvidenceState.KNOWN:
            if (
                self.timestamp_utc is None
                or self.observed_at_utc is not None
                or self.exception_id is not None
            ):
                raise ValueError(
                    "Known acquisition requires its timestamp and no unknown-state fields."
                )
            return
        if self.timestamp_utc is not None or self.observed_at_utc is None or not self.exception_id:
            raise ValueError("Unknown acquisition requires an observation time and exception ID.")

    @classmethod
    def known(cls, timestamp_utc: datetime) -> Acquisition:
        return cls(state=EvidenceState.KNOWN, timestamp_utc=timestamp_utc)

    @classmethod
    def unknown(cls, observed_at_utc: datetime, exception_id: str) -> Acquisition:
        return cls(
            state=EvidenceState.UNKNOWN,
            observed_at_utc=observed_at_utc,
            exception_id=exception_id,
        )

    @property
    def is_known(self) -> bool:
        return self.state is EvidenceState.KNOWN

    @property
    def sort_timestamp_utc(self) -> datetime:
        if self.is_known:
            return self.require_timestamp_utc()
        if self.observed_at_utc is None:
            raise ValueError("Unknown acquisition has no observation timestamp.")
        return self.observed_at_utc

    def require_timestamp_utc(self) -> datetime:
        if not self.is_known or self.timestamp_utc is None:
            raise ValueError("Unknown acquisition has no supported acquisition timestamp.")
        return self.timestamp_utc

    def require_exception_id(self) -> str:
        if self.state is not EvidenceState.UNKNOWN or self.exception_id is None:
            raise ValueError("Known acquisition has no blocking exception ID.")
        return self.exception_id

@dataclass(frozen=True, slots=True)
class Lot:
    """One wallet-and-mint inventory parcel with quantity, basis, and lineage.

    Starting fields retain what entered the lot; remaining fields hold current availability.
    Parent identity traces transferred or converted child inventory to its source lot.
    """

    lot_id: str
    wallet_id: str
    mint: str
    symbol: str
    acquisition: Acquisition
    starting_quantity_base_units: int
    remaining_quantity_base_units: int
    starting_basis: Basis
    remaining_basis: Basis
    source_event_id: str
    parent_lot_id: str | None = None
    state: InventoryState = InventoryState.OPEN

@dataclass(frozen=True, slots=True)
class LotConsumption:
    """One disposal's exact quantity and basis decrease from a source lot.

    A multi-lot disposal creates one consumption per slice so report rows can retain distinct
    acquisition dates, basis values, and lineage.
    """

    consumption_id: str
    disposal_event_id: str
    lot_id: str
    quantity_base_units: int
    consumed_basis: Basis

@dataclass(frozen=True, slots=True)
class SpecificLotSelection:
    """One explicit source-lot quantity requested for a disposal.

    The small value object validates each positive slice before the enclosing manifest checks
    event, wallet, mint, and total selection consistency.
    """

    lot_id: str
    quantity_base_units: int

    def __post_init__(self) -> None:
        if (
            not self.lot_id
            or isinstance(self.quantity_base_units, bool)
            or not isinstance(self.quantity_base_units, int)
            or self.quantity_base_units <= 0
        ):
            raise ValueError("A specific lot selection requires a lot ID and positive quantity.")

@dataclass(frozen=True, slots=True)
class SpecificIdentificationManifest:
    """Explicit wallet, mint, event, lot, and quantity selections with no fallback.

    Binding selections to their disposal context prevents valid lot IDs from being reused for a
    different wallet or asset and mistaken for adequate identification.
    """

    disposal_event_id: str
    wallet_id: str
    mint: str
    selections: tuple[SpecificLotSelection, ...]

    def __post_init__(self) -> None:
        if not self.disposal_event_id or not self.wallet_id or not self.mint:
            raise ValueError("A specific-identification manifest requires event, wallet, and mint.")
        if not self.selections or any(
            not isinstance(item, SpecificLotSelection) for item in self.selections
        ):
            raise ValueError("A specific-identification manifest requires typed selections.")
        lot_ids = [item.lot_id for item in self.selections]
        if len(lot_ids) != len(set(lot_ids)):
            raise ValueError("A specific-identification manifest cannot repeat a source lot.")

@dataclass(frozen=True, slots=True)
class Swap:
    """Linked disposal, acquisition, valuation, and fee results for one swap.

    Keeping both token legs under one parent prevents the outgoing consumption and received lot
    from drifting into unrelated accounting outcomes when valuation is blocked.
    """

    valuation_state: EvidenceState
    event_id: str
    given_leg: TokenLeg
    received_leg: TokenLeg
    fee: FeeEvidence
    exchange_value_usd: Decimal | None
    price_source_ids: tuple[str, ...]
    consumptions: tuple[LotConsumption, ...]
    received_lot: Lot
    valuation_exception_id: str | None = None

    def __post_init__(self) -> None:
        if self.given_leg.direction is not Direction.OUTBOUND:
            raise ValueError("Swap given leg must be outbound.")
        if self.received_leg.direction is not Direction.INBOUND:
            raise ValueError("Swap received leg must be inbound.")
        if self.received_lot.source_event_id != self.event_id or any(
            item.disposal_event_id != self.event_id for item in self.consumptions
        ):
            raise ValueError("Swap accounting components must share the parent event ID.")
        if self.valuation_state is EvidenceState.KNOWN:
            if (
                not isinstance(self.exchange_value_usd, Decimal)
                or not self.price_source_ids
                or self.valuation_exception_id is not None
            ):
                raise ValueError(
                    "Valued swap requires a Decimal value, price sources, and no exception."
                )
            return
        if self.exchange_value_usd is not None or not self.valuation_exception_id:
            raise ValueError("Unvalued swap requires a valuation exception and no value.")

    @property
    def is_valued(self) -> bool:
        return self.valuation_state is EvidenceState.KNOWN

@dataclass(frozen=True, slots=True)
class Fee:
    """A proven charged fee, its valuation, inventory use, and context treatment.

    The model links the fee's own token consumption to a single acquisition, disposal, failed,
    standalone, or unsupported treatment so value cannot be applied twice.
    """

    valuation_state: EvidenceState
    context: FeeContext
    parent_event_id: str
    fee_event: NormalizedEvent
    fee_value_usd: Decimal | None
    price_source_id: str | None
    consumptions: tuple[LotConsumption, ...]
    related_lot_id: str | None = None
    adjusted_proceeds_usd: Decimal | None = None
    valuation_exception_id: str | None = None
    treatment_exception_id: str | None = None

    def __post_init__(self) -> None:
        if self.fee_event.instruction_type != "fee":
            raise ValueError("Fee accounting requires a derived fee event.")
        if self.valuation_state is EvidenceState.KNOWN:
            if (
                not isinstance(self.fee_value_usd, Decimal)
                or not self.price_source_id
                or self.valuation_exception_id is not None
            ):
                raise ValueError(
                    "Valued fee requires a Decimal value, one price source, "
                    "and no valuation exception."
                )
            return
        if (
            self.fee_value_usd is not None
            or self.price_source_id is not None
            or not self.valuation_exception_id
        ):
            raise ValueError("Unvalued fee requires a valuation exception and no dollar value.")

    @property
    def is_valued(self) -> bool:
        return self.valuation_state is EvidenceState.KNOWN

    @property
    def treatment_is_supported(self) -> bool:
        return self.treatment_exception_id is None

@dataclass(frozen=True, slots=True)
class Income:
    """Jurisdiction-neutral valued receipt and its created inventory lot.

    Fair value and created-lot identity stay together so reconciliation can prove recognized
    income becomes equal starting basis for the received property.
    """

    event_id: str
    source_id: str
    wallet_id: str
    leg: TokenLeg
    category: ClassificationType
    quantity: Decimal
    received_at: datetime
    price_usd: Decimal
    price_source_id: str
    fair_value_usd: Decimal
    created_lot_id: str

@dataclass(frozen=True, slots=True)
class Wrap:
    """Supported continuity movement or exception-backed ambiguous transformation.

    The type preserves observed source and destination legs without asserting that every wrap is
    non-taxable; only the selected one-to-one policy path carries basis and date.
    """

    event_id: str
    source_leg: TokenLeg
    destination_leg: TokenLeg
    movements: tuple[LotMovement, ...]
    destination_lots: tuple[Lot, ...]
    exception_id: str | None = None

    @property
    def is_continuous(self) -> bool:
        return self.exception_id is None

@dataclass(frozen=True, slots=True)
class LotMovement:
    """Inventory moved without a sale while retaining source-lot lineage.

    Recording source and destination lots, wallets, basis, quantity, event, and state lets
    reconciliation prove that movement relocated inventory rather than creating or deleting it.
    """

    movement_id: str
    event_id: str
    source_lot_id: str
    destination_lot_id: str
    source_wallet_id: str
    destination_wallet_id: str
    mint: str
    quantity_base_units: int
    moved_basis: Basis
    destination_state: InventoryState
