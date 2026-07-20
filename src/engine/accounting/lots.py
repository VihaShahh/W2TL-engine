"""Maintain exact wallet-and-mint inventory and immutable lot lineage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from engine.core.identity import stable_id
from engine.models import (
    Acquisition,
    Basis,
    Direction,
    HoldingTerm,
    InventoryState,
    Lot,
    LotConsumption,
    LotMovement,
    NormalizedEvent,
    SpecificIdentificationManifest,
    TokenLeg,
)
from engine.policy.config import HoldingTermRule


class InsufficientInventoryError(ValueError):
    """Raised before an operation could make represented inventory negative."""

class UnrankableHifoBasisError(ValueError):
    """Raised before HIFO could assign an artificial rank to unknown basis."""

class SpecificIdentificationError(ValueError):
    """Raised before an invalid or absent lot-selection manifest could mutate inventory."""

class MissingSpecificIdentificationError(SpecificIdentificationError):
    """Raised when specific identification is invoked without an explicit manifest."""

class InvalidSpecificIdentificationError(SpecificIdentificationError):
    """Raised when a specific-identification manifest conflicts with current inventory."""

@dataclass(frozen=True, slots=True)
class _SelectedSlice:
    lot: Lot
    quantity_base_units: int
    basis: Basis

def holding_term(
    acquisition: Acquisition,
    disposed_at: datetime,
    *,
    rule: HoldingTermRule,
) -> HoldingTerm | None:
    """Return long only after the one-year anniversary; unknown dates block a term."""
    if rule is not HoldingTermRule.MORE_THAN_ONE_YEAR:
        raise ValueError(f"Unsupported holding-term rule: {rule.value}.")
    if not acquisition.is_known:
        return None
    acquired_at = acquisition.require_timestamp_utc()
    try:
        anniversary = acquired_at.replace(year=acquired_at.year + 1)
    except ValueError:
        anniversary = acquired_at.replace(year=acquired_at.year + 1, day=28)
    return HoldingTerm.LONG if disposed_at > anniversary else HoldingTerm.SHORT

class TaxLotLedger(ABC):
    """Own shared tax-lot mutations for one allocation method.

    Subclasses decide only which eligible lots are consumed first. Acquisition, splitting,
    movement, suspense, and conservation stay here so another method cannot accidentally
    change the accounting mechanics while changing lot order.
    """

    def __init__(self) -> None:
        self._lots: dict[tuple[str, str], list[Lot]] = {}
        self._consumptions: list[LotConsumption] = []
        self._movements: list[LotMovement] = []

    @property
    @abstractmethod
    def method(self) -> str:
        """Return the stable allocation-method label used by traces and outputs."""

    @abstractmethod
    def _get_lots_for_consumption(self, lots: tuple[Lot, ...]) -> tuple[Lot, ...]:
        """Order an already eligible wallet-and-mint lot set for consumption."""

    @staticmethod
    def _single_leg(event: NormalizedEvent, *, direction: Direction) -> TokenLeg:
        if len(event.token_legs) != 1 or event.token_legs[0].direction is not direction:
            raise ValueError(f"Event {event.event_id} must have one {direction.value} token leg.")
        return event.token_legs[0]

    @classmethod
    def _component_leg(
        cls,
        event: NormalizedEvent,
        *,
        direction: Direction,
        leg: TokenLeg | None,
    ) -> TokenLeg:
        if leg is None:
            return cls._single_leg(event, direction=direction)
        if leg not in event.token_legs or leg.direction is not direction:
            raise ValueError(
                f"Event {event.event_id} does not contain the selected {direction.value} token leg."
            )
        return leg

    def _store(self, lot: Lot) -> Lot:
        queue = self._lots.setdefault((lot.wallet_id, lot.mint), [])
        queue.append(lot)
        queue.sort(
            key=lambda item: (
                item.acquisition.sort_timestamp_utc,
                item.lot_id,
            )
        )
        return lot

    @staticmethod
    def _validate_lots_for_consumption(
        *, eligible: tuple[Lot, ...], ordered: tuple[Lot, ...]
    ) -> None:
        """Ensure a subclass reorders eligible lots without adding or dropping inventory."""
        eligible_ids = [lot.lot_id for lot in eligible]
        ordered_ids = [lot.lot_id for lot in ordered]
        if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(eligible_ids):
            raise ValueError(
                "_get_lots_for_consumption() must return every eligible lot exactly once."
            )

    def acquire(
        self,
        event: NormalizedEvent,
        *,
        leg: TokenLeg | None = None,
        basis_usd: Decimal | None = None,
    ) -> Lot:
        """Create one open lot when both acquisition quantity and basis are known."""
        selected_leg = self._component_leg(event, direction=Direction.INBOUND, leg=leg)
        selected_basis = basis_usd if basis_usd is not None else event.cash_value_usd
        if selected_basis is None:
            raise ValueError(f"Acquisition event {event.event_id} has no known basis.")
        return self._store(
            Lot(
                lot_id=stable_id("lot", event.event_id, event.wallet_id, selected_leg.mint),
                wallet_id=event.wallet_id,
                mint=selected_leg.mint,
                symbol=selected_leg.symbol,
                acquisition=Acquisition.known(event.timestamp_utc),
                starting_quantity_base_units=selected_leg.quantity_base_units,
                remaining_quantity_base_units=selected_leg.quantity_base_units,
                starting_basis=Basis.known(selected_basis),
                remaining_basis=Basis.known(selected_basis),
                source_event_id=event.event_id,
            )
        )

    def acquire_unknown(
        self,
        event: NormalizedEvent,
        *,
        exception_id: str,
        leg: TokenLeg | None = None,
        acquisition: Acquisition | None = None,
        state: InventoryState = InventoryState.OPEN,
    ) -> Lot:
        """Preserve received quantity without converting missing basis into zero."""
        selected_leg = self._component_leg(event, direction=Direction.INBOUND, leg=leg)
        unknown = Basis.unknown(exception_id)
        return self._store(
            Lot(
                lot_id=stable_id(
                    "lot", event.event_id, event.wallet_id, selected_leg.mint, state.value
                ),
                wallet_id=event.wallet_id,
                mint=selected_leg.mint,
                symbol=selected_leg.symbol,
                acquisition=acquisition or Acquisition.known(event.timestamp_utc),
                starting_quantity_base_units=selected_leg.quantity_base_units,
                remaining_quantity_base_units=selected_leg.quantity_base_units,
                starting_basis=unknown,
                remaining_basis=unknown,
                source_event_id=event.event_id,
                state=state,
            )
        )

    def adjust_basis(self, lot_id: str, *, adjustment_usd: Decimal) -> Lot:
        """Apply one exact acquisition adjustment before any units leave the lot."""
        if not isinstance(adjustment_usd, Decimal) or adjustment_usd < 0:
            raise ValueError("Basis adjustment must be a non-negative Decimal.")
        for queue in self._lots.values():
            for index, lot in enumerate(queue):
                if lot.lot_id != lot_id:
                    continue
                if lot.remaining_quantity_base_units != lot.starting_quantity_base_units:
                    raise ValueError("Basis cannot be adjusted after lot consumption.")
                if not lot.starting_basis.is_known or not lot.remaining_basis.is_known:
                    raise ValueError("Unknown basis cannot receive a numeric adjustment.")
                adjusted = replace(
                    lot,
                    starting_basis=Basis.known(
                        lot.starting_basis.require_total_usd() + adjustment_usd
                    ),
                    remaining_basis=Basis.known(
                        lot.remaining_basis.require_total_usd() + adjustment_usd
                    ),
                )
                queue[index] = adjusted
                return adjusted
        raise KeyError(f"Unknown lot ID: {lot_id}.")

    def mark_basis_unknown(self, lot_id: str, *, exception_id: str) -> Lot:
        """Block a not-yet-consumed acquisition whose required adjustment is unresolved."""
        for queue in self._lots.values():
            for index, lot in enumerate(queue):
                if lot.lot_id != lot_id:
                    continue
                if lot.remaining_quantity_base_units != lot.starting_quantity_base_units:
                    raise ValueError("Consumed lot basis cannot be replaced with unknown state.")
                unknown = Basis.unknown(exception_id)
                adjusted = replace(lot, starting_basis=unknown, remaining_basis=unknown)
                queue[index] = adjusted
                return adjusted
        raise KeyError(f"Unknown lot ID: {lot_id}.")

    @staticmethod
    def _split_basis(
        basis: Basis, *, quantity: int, available_quantity: int
    ) -> tuple[Basis, Basis]:
        if not basis.is_known:
            return basis, basis
        total_usd = basis.require_total_usd()
        if quantity == available_quantity:
            consumed = total_usd
        else:
            consumed = total_usd * (Decimal(quantity) / Decimal(available_quantity))
        # Subtraction preserves the exact original total across repeated partial splits.
        return Basis.known(consumed), Basis.known(total_usd - consumed)

    def _select(
        self, *, wallet_id: str, mint: str, quantity_base_units: int
    ) -> tuple[_SelectedSlice, ...]:
        key = (wallet_id, mint)
        queue = self._lots.get(key, [])
        eligible = tuple(
            lot
            for lot in queue
            if lot.state is InventoryState.OPEN and lot.remaining_quantity_base_units > 0
        )
        available = sum(lot.remaining_quantity_base_units for lot in eligible)
        if available < quantity_base_units:
            raise InsufficientInventoryError(
                f"{wallet_id}/{mint} needs {quantity_base_units} base units but has {available}."
            )

        ordered = self._get_lots_for_consumption(eligible)
        self._validate_lots_for_consumption(eligible=eligible, ordered=ordered)
        lot_indexes = {lot.lot_id: index for index, lot in enumerate(queue)}
        remaining = quantity_base_units
        selected: list[_SelectedSlice] = []
        for lot in ordered:
            if remaining == 0:
                break
            index = lot_indexes[lot.lot_id]
            quantity = min(remaining, lot.remaining_quantity_base_units)
            consumed_basis, remaining_basis = self._split_basis(
                lot.remaining_basis,
                quantity=quantity,
                available_quantity=lot.remaining_quantity_base_units,
            )
            queue[index] = replace(
                lot,
                remaining_quantity_base_units=lot.remaining_quantity_base_units - quantity,
                remaining_basis=remaining_basis,
            )
            selected.append(
                _SelectedSlice(lot=lot, quantity_base_units=quantity, basis=consumed_basis)
            )
            remaining -= quantity
        return tuple(selected)

    def _select_specific(
        self,
        *,
        event: NormalizedEvent,
        manifest: SpecificIdentificationManifest,
    ) -> tuple[_SelectedSlice, ...]:
        selected_leg = self._single_leg(event, direction=Direction.OUTBOUND)
        if manifest.disposal_event_id != event.event_id:
            raise InvalidSpecificIdentificationError(
                "Specific-identification manifest names a different disposal event."
            )
        if manifest.wallet_id != event.wallet_id:
            raise InvalidSpecificIdentificationError(
                "Specific-identification manifest names a different disposing wallet."
            )
        if manifest.mint != selected_leg.mint:
            raise InvalidSpecificIdentificationError(
                "Specific-identification manifest names a different disposal mint."
            )
        selected_quantity = sum(
            item.quantity_base_units for item in manifest.selections
        )
        if selected_quantity != selected_leg.quantity_base_units:
            raise InvalidSpecificIdentificationError(
                "Specific-identification quantities do not equal the disposal quantity."
            )

        validated: list[tuple[list[Lot], int, Lot, int]] = []
        for selection in manifest.selections:
            found: tuple[list[Lot], int, Lot] | None = None
            for queue in self._lots.values():
                for index, lot in enumerate(queue):
                    if lot.lot_id == selection.lot_id:
                        found = queue, index, lot
                        break
                if found is not None:
                    break
            if found is None:
                raise InvalidSpecificIdentificationError(
                    f"Specific-identification lot does not exist: {selection.lot_id}."
                )
            queue, index, lot = found
            if lot.wallet_id != manifest.wallet_id:
                raise InvalidSpecificIdentificationError(
                    f"Specific-identification lot belongs to another wallet: {lot.lot_id}."
                )
            if lot.mint != manifest.mint:
                raise InvalidSpecificIdentificationError(
                    f"Specific-identification lot has another mint: {lot.lot_id}."
                )
            if lot.state is not InventoryState.OPEN:
                raise InvalidSpecificIdentificationError(
                    f"Specific-identification lot is not open: {lot.lot_id}."
                )
            if lot.remaining_quantity_base_units < selection.quantity_base_units:
                raise InvalidSpecificIdentificationError(
                    f"Specific-identification lot has insufficient remaining quantity: "
                    f"{lot.lot_id}."
                )
            validated.append((queue, index, lot, selection.quantity_base_units))

        selected: list[_SelectedSlice] = []
        for queue, index, lot, quantity in validated:
            consumed_basis, remaining_basis = self._split_basis(
                lot.remaining_basis,
                quantity=quantity,
                available_quantity=lot.remaining_quantity_base_units,
            )
            queue[index] = replace(
                lot,
                remaining_quantity_base_units=lot.remaining_quantity_base_units - quantity,
                remaining_basis=remaining_basis,
            )
            selected.append(
                _SelectedSlice(
                    lot=lot,
                    quantity_base_units=quantity,
                    basis=consumed_basis,
                )
            )
        return tuple(selected)

    def consume(
        self, event: NormalizedEvent, *, leg: TokenLeg | None = None
    ) -> tuple[LotConsumption, ...]:
        """Consume ordered open slices, including explicit unknown-basis slices."""
        selected_leg = self._component_leg(event, direction=Direction.OUTBOUND, leg=leg)
        slices = self._select(
            wallet_id=event.wallet_id,
            mint=selected_leg.mint,
            quantity_base_units=selected_leg.quantity_base_units,
        )
        consumptions = tuple(
            LotConsumption(
                consumption_id=stable_id("con", event.event_id, item.lot.lot_id, index),
                disposal_event_id=event.event_id,
                lot_id=item.lot.lot_id,
                quantity_base_units=item.quantity_base_units,
                consumed_basis=item.basis,
            )
            for index, item in enumerate(slices)
        )
        self._consumptions.extend(consumptions)
        return consumptions

    def _move(
        self,
        event: NormalizedEvent,
        *,
        destination_wallet_id: str,
        destination_state: InventoryState,
        leg: TokenLeg | None = None,
        destination_leg: TokenLeg | None = None,
    ) -> tuple[LotMovement, ...]:
        source_leg = self._component_leg(event, direction=Direction.OUTBOUND, leg=leg)
        slices = self._select(
            wallet_id=event.wallet_id,
            mint=source_leg.mint,
            quantity_base_units=source_leg.quantity_base_units,
        )
        movements: list[LotMovement] = []
        for index, item in enumerate(slices):
            destination_mint = (
                destination_leg.mint if destination_leg is not None else item.lot.mint
            )
            destination_symbol = (
                destination_leg.symbol if destination_leg is not None else item.lot.symbol
            )
            lot_id_parts: tuple[str | int, ...] = (
                event.event_id,
                destination_wallet_id,
                item.lot.lot_id,
                index,
            )
            if destination_leg is not None:
                lot_id_parts = (*lot_id_parts, destination_mint)
            destination_lot = self._store(
                Lot(
                    lot_id=stable_id("lot", *lot_id_parts),
                    wallet_id=destination_wallet_id,
                    mint=destination_mint,
                    symbol=destination_symbol,
                    acquisition=item.lot.acquisition,
                    starting_quantity_base_units=item.quantity_base_units,
                    remaining_quantity_base_units=item.quantity_base_units,
                    starting_basis=item.basis,
                    remaining_basis=item.basis,
                    source_event_id=event.event_id,
                    parent_lot_id=item.lot.lot_id,
                    state=destination_state,
                )
            )
            movements.append(
                LotMovement(
                    movement_id=stable_id(
                        "mov", event.event_id, item.lot.lot_id, destination_lot.lot_id
                    ),
                    event_id=event.event_id,
                    source_lot_id=item.lot.lot_id,
                    destination_lot_id=destination_lot.lot_id,
                    source_wallet_id=event.wallet_id,
                    destination_wallet_id=destination_wallet_id,
                    mint=item.lot.mint,
                    quantity_base_units=item.quantity_base_units,
                    moved_basis=item.basis,
                    destination_state=destination_state,
                )
            )
        result = tuple(movements)
        self._movements.extend(result)
        return result

    def move(
        self, event: NormalizedEvent, *, destination_wallet_id: str
    ) -> tuple[LotMovement, ...]:
        """Carry selected slices to another owned wallet without a taxable result."""
        if destination_wallet_id == event.wallet_id:
            raise ValueError("Owned-wallet movement requires a different destination wallet.")
        return self._move(
            event,
            destination_wallet_id=destination_wallet_id,
            destination_state=InventoryState.OPEN,
        )

    def suspend(self, event: NormalizedEvent) -> tuple[LotMovement, ...]:
        """Remove uncertain outbound units from spendable inventory without calling it a sale."""
        return self._move(
            event,
            destination_wallet_id=event.wallet_id,
            destination_state=InventoryState.SUSPENSE,
        )

    def suspend_leg(
        self, event: NormalizedEvent, *, leg: TokenLeg
    ) -> tuple[LotMovement, ...]:
        """Suspend one outbound component of a multi-leg unresolved event."""
        return self._move(
            event,
            destination_wallet_id=event.wallet_id,
            destination_state=InventoryState.SUSPENSE,
            leg=leg,
        )

    def convert(
        self,
        event: NormalizedEvent,
        *,
        source_leg: TokenLeg,
        destination_leg: TokenLeg,
    ) -> tuple[LotMovement, ...]:
        """Carry one-to-one source slices into a new mint for supported continuity."""
        if source_leg.decimals != destination_leg.decimals:
            raise ValueError("Continuity conversion requires matching token decimals.")
        if source_leg.quantity_base_units != destination_leg.quantity_base_units:
            raise ValueError("Continuity conversion requires equal base-unit quantity.")
        return self._move(
            event,
            destination_wallet_id=event.wallet_id,
            destination_state=InventoryState.OPEN,
            leg=source_leg,
            destination_leg=destination_leg,
        )

    def get_lot(self, lot_id: str) -> Lot:
        """Return the current lot state by stable ID for source lineage."""
        for queue in self._lots.values():
            for lot in queue:
                if lot.lot_id == lot_id:
                    return lot
        raise KeyError(f"Unknown lot ID: {lot_id}.")

    def open_lots(self) -> tuple[Lot, ...]:
        """Return spendable non-empty lots in deterministic queue order."""
        return tuple(
            lot
            for key in sorted(self._lots)
            for lot in self._lots[key]
            if lot.state is InventoryState.OPEN and lot.remaining_quantity_base_units > 0
        )

    def all_lots(self) -> tuple[Lot, ...]:
        """Return open, depleted, suspended, and quarantined lots for reconciliation."""
        return tuple(lot for key in sorted(self._lots) for lot in self._lots[key])

    def all_consumptions(self) -> tuple[LotConsumption, ...]:
        """Return every taxable consumption slice in deterministic processing order."""
        return tuple(self._consumptions)

    def all_movements(self) -> tuple[LotMovement, ...]:
        """Return every internal, suspense, and continuity movement."""
        return tuple(self._movements)

class FifoLedger(TaxLotLedger):
    """Select the oldest eligible lots while reusing shared tax-lot mutations."""

    @property
    def method(self) -> str:
        return "FIFO"

    def _get_lots_for_consumption(self, lots: tuple[Lot, ...]) -> tuple[Lot, ...]:
        """Order by original acquisition evidence, then stable lot ID for exact ties."""
        return tuple(
            sorted(
                lots,
                key=lambda lot: (
                    lot.acquisition.sort_timestamp_utc,
                    lot.lot_id,
                ),
            )
        )

class HifoLedger(TaxLotLedger):
    """Select the highest exact remaining unit basis within the eligible lot set."""

    @property
    def method(self) -> str:
        return "HIFO"

    def _get_lots_for_consumption(self, lots: tuple[Lot, ...]) -> tuple[Lot, ...]:
        unknown_lot_ids = sorted(
            lot.lot_id for lot in lots if not lot.remaining_basis.is_known
        )
        if unknown_lot_ids:
            raise UnrankableHifoBasisError(
                "HIFO cannot rank unknown-basis eligible lot(s): "
                + ", ".join(unknown_lot_ids)
                + "."
            )
        return tuple(
            sorted(
                lots,
                key=lambda lot: (
                    -lot.remaining_basis.require_total_usd()
                    / Decimal(lot.remaining_quantity_base_units),
                    lot.acquisition.sort_timestamp_utc,
                    lot.lot_id,
                ),
            )
        )

class SpecificIdentificationLedger(TaxLotLedger):
    """Consume only explicit valid manifest slices and never choose an automatic fallback."""

    @property
    def method(self) -> str:
        return "SPECIFIC_IDENTIFICATION"

    def _get_lots_for_consumption(self, lots: tuple[Lot, ...]) -> tuple[Lot, ...]:
        raise MissingSpecificIdentificationError(
            "Specific identification requires an explicit disposal manifest."
        )

    def consume_manifest(
        self,
        event: NormalizedEvent,
        *,
        manifest: SpecificIdentificationManifest,
    ) -> tuple[LotConsumption, ...]:
        """Validate all requested slices before applying shared basis-split mutations."""
        slices = self._select_specific(event=event, manifest=manifest)
        consumptions = tuple(
            LotConsumption(
                consumption_id=stable_id("con", event.event_id, item.lot.lot_id, index),
                disposal_event_id=event.event_id,
                lot_id=item.lot.lot_id,
                quantity_base_units=item.quantity_base_units,
                consumed_basis=item.basis,
            )
            for index, item in enumerate(slices)
        )
        self._consumptions.extend(consumptions)
        return consumptions
