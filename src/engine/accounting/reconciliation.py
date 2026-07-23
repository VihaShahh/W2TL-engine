"""Enforce whole-run accounting and report invariants before serialization."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from engine.accounting.lots import TaxLotLedger
from engine.models import DisposalRow, IncomeRow


class ReconciliationError(RuntimeError):
    """Raised when a completed run cannot support its report outputs."""

@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Counts of accounting objects that passed report-boundary checks.

    The result proves reconciliation ran over the stated population; policy correctness still
    relies on reviewed ground truth, scenario tests, and domain invariants.
    """

    lot_count: int
    consumption_count: int
    movement_count: int
    disposal_count: int
    income_count: int

def _require_finite(name: str, value: Decimal) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ReconciliationError(f"{name} must be a finite Decimal, got {value!r}.")

def reconcile_run(
    *,
    ledger: TaxLotLedger,
    disposals: tuple[DisposalRow, ...],
    income: tuple[IncomeRow, ...],
) -> ReconciliationResult:
    """Verify inventory, consumption lineage, disposal equations, and income basis."""
    lots = ledger.all_lots()
    lots_by_id = {lot.lot_id: lot for lot in lots}
    if len(lots_by_id) != len(lots):
        raise ReconciliationError("Lot IDs are not unique.")

    # First validate every lot as a possible before/after accounting state. Known basis can only
    # decrease from its supported starting total; unknown basis must never become numeric.
    for lot in lots:
        if not 0 <= lot.remaining_quantity_base_units <= lot.starting_quantity_base_units:
            raise ReconciliationError(
                f"Lot {lot.lot_id} has invalid remaining quantity "
                f"{lot.remaining_quantity_base_units}."
            )
        if lot.starting_basis.is_known:
            if not lot.remaining_basis.is_known:
                raise ReconciliationError(
                    f"Known-basis lot {lot.lot_id} has unknown remaining basis."
                )
            starting_basis = lot.starting_basis.require_total_usd()
            remaining_basis = lot.remaining_basis.require_total_usd()
            _require_finite(f"Lot {lot.lot_id} starting basis", starting_basis)
            _require_finite(f"Lot {lot.lot_id} remaining basis", remaining_basis)
            if remaining_basis < 0 or remaining_basis > starting_basis:
                raise ReconciliationError(
                    f"Lot {lot.lot_id} has invalid remaining basis {remaining_basis}."
                )
        elif lot.remaining_basis.is_known:
            raise ReconciliationError(
                f"Unknown-basis lot {lot.lot_id} gained unsupported numeric basis."
            )

    consumptions = ledger.all_consumptions()
    consumptions_by_id = {item.consumption_id: item for item in consumptions}
    if len(consumptions_by_id) != len(consumptions):
        raise ReconciliationError("Consumption IDs are not unique.")
    movements = ledger.all_movements()
    if len({item.movement_id for item in movements}) != len(movements):
        raise ReconciliationError("Movement IDs are not unique.")

    # Build an independent explanation of each source lot's decreases. Later we compare these
    # consumptions and movements with starting minus remaining state for exact conservation.
    quantity_decreases: dict[str, int] = {}
    basis_decreases: dict[str, Decimal] = {}
    for consumption in consumptions:
        if consumption.quantity_base_units <= 0 or consumption.lot_id not in lots_by_id:
            raise ReconciliationError(
                f"Consumption {consumption.consumption_id} has invalid source inventory."
            )
        quantity_decreases[consumption.lot_id] = (
            quantity_decreases.get(consumption.lot_id, 0)
            + consumption.quantity_base_units
        )
        if consumption.consumed_basis.is_known:
            consumed_basis = consumption.consumed_basis.require_total_usd()
            _require_finite(
                f"Consumption {consumption.consumption_id} basis", consumed_basis
            )
            basis_decreases[consumption.lot_id] = (
                basis_decreases.get(consumption.lot_id, Decimal(0)) + consumed_basis
            )

    # A movement is a paired source decrease and destination child creation. The child must carry
    # acquisition and basis rather than manufacturing a new client-wide acquisition.
    movement_destinations: set[str] = set()
    for movement in movements:
        source_lot = lots_by_id.get(movement.source_lot_id)
        destination_lot = lots_by_id.get(movement.destination_lot_id)
        if source_lot is None or destination_lot is None:
            raise ReconciliationError(
                f"Movement {movement.movement_id} references unknown inventory."
            )
        if movement.destination_lot_id in movement_destinations:
            raise ReconciliationError(
                f"Lot {movement.destination_lot_id} has multiple creating movements."
            )
        movement_destinations.add(movement.destination_lot_id)
        if (
            movement.quantity_base_units <= 0
            or movement.event_id != destination_lot.source_event_id
            or movement.source_wallet_id != source_lot.wallet_id
            or movement.destination_wallet_id != destination_lot.wallet_id
            or movement.mint != source_lot.mint
            or movement.quantity_base_units
            != destination_lot.starting_quantity_base_units
            or destination_lot.parent_lot_id != source_lot.lot_id
            or destination_lot.state is not movement.destination_state
            or destination_lot.acquisition != source_lot.acquisition
            or destination_lot.starting_basis != movement.moved_basis
        ):
            raise ReconciliationError(
                f"Movement {movement.movement_id} does not reconcile its source and destination."
            )
        quantity_decreases[source_lot.lot_id] = (
            quantity_decreases.get(source_lot.lot_id, 0)
            + movement.quantity_base_units
        )
        if movement.moved_basis.is_known:
            moved_basis = movement.moved_basis.require_total_usd()
            _require_finite(f"Movement {movement.movement_id} basis", moved_basis)
            basis_decreases[source_lot.lot_id] = (
                basis_decreases.get(source_lot.lot_id, Decimal(0)) + moved_basis
            )

    # No lot may lose quantity or known basis without one of the explanations accumulated above.
    for lot in lots:
        expected_decrease = (
            lot.starting_quantity_base_units - lot.remaining_quantity_base_units
        )
        if quantity_decreases.get(lot.lot_id, 0) != expected_decrease:
            raise ReconciliationError(
                f"Lot {lot.lot_id} quantity decrease is not fully explained."
            )
        if lot.starting_basis.is_known:
            expected_basis_decrease = (
                lot.starting_basis.require_total_usd()
                - lot.remaining_basis.require_total_usd()
            )
            if basis_decreases.get(lot.lot_id, Decimal(0)) != expected_basis_decrease:
                raise ReconciliationError(
                    f"Lot {lot.lot_id} basis decrease is not fully explained."
                )
    if len({row.disposal_id for row in disposals}) != len(disposals):
        raise ReconciliationError("Disposal row IDs are not unique.")

    # Report rows are projections of consumptions, not a second accounting system. Enforce the
    # gain equation and require one-to-one quantity, basis, lot, wallet, mint, and date lineage.
    seen_consumptions: set[str] = set()
    for disposal_row in disposals:
        if disposal_row.quantity_base_units <= 0:
            raise ReconciliationError(
                f"Disposal {disposal_row.disposal_id} has non-positive quantity."
            )
        for field_name, value in (
            ("quantity", disposal_row.quantity),
            ("proceeds", disposal_row.proceeds_usd),
            ("cost basis", disposal_row.cost_basis_usd),
            ("gain or loss", disposal_row.gain_loss_usd),
        ):
            _require_finite(f"Disposal {disposal_row.disposal_id} {field_name}", value)
        if disposal_row.gain_loss_usd != (
            disposal_row.proceeds_usd - disposal_row.cost_basis_usd
        ):
            raise ReconciliationError(
                f"Disposal {disposal_row.disposal_id} does not satisfy proceeds minus basis."
            )
        row_consumption = consumptions_by_id.get(disposal_row.consumption_id)
        if row_consumption is None:
            raise ReconciliationError(
                f"Disposal {disposal_row.disposal_id} references unknown consumption "
                f"{disposal_row.consumption_id}."
            )
        if disposal_row.consumption_id in seen_consumptions:
            raise ReconciliationError(
                f"Consumption {disposal_row.consumption_id} appears in multiple disposal rows."
            )
        seen_consumptions.add(disposal_row.consumption_id)
        if (
            row_consumption.disposal_event_id != disposal_row.event_id
            or row_consumption.lot_id != disposal_row.lot_id
            or row_consumption.quantity_base_units != disposal_row.quantity_base_units
            or not row_consumption.consumed_basis.is_known
            or row_consumption.consumed_basis.require_total_usd()
            != disposal_row.cost_basis_usd
        ):
            raise ReconciliationError(
                f"Disposal {disposal_row.disposal_id} does not match its consumption lineage."
            )
        source_lot = lots_by_id.get(disposal_row.lot_id)
        if source_lot is None or not source_lot.acquisition.is_known:
            raise ReconciliationError(
                f"Disposal {disposal_row.disposal_id} references an unsupported source lot."
            )
        if (
            source_lot.wallet_id != disposal_row.wallet_id
            or source_lot.mint != disposal_row.mint
            or source_lot.acquisition.require_timestamp_utc()
            != disposal_row.date_acquired
        ):
            raise ReconciliationError(
                f"Disposal {disposal_row.disposal_id} does not match its lot identity."
            )

    if len({row.income_id for row in income}) != len(income):
        raise ReconciliationError("Income row IDs are not unique.")
    # Recognized receipt value becomes the received property's starting basis. Checking that link
    # prevents current income and a future disposal from using inconsistent dollar amounts.
    for income_row in income:
        for field_name, value in (
            ("quantity", income_row.quantity),
            ("price", income_row.price_usd),
            ("fair value", income_row.fair_value_usd),
        ):
            _require_finite(f"Income {income_row.income_id} {field_name}", value)
        if (
            income_row.quantity_base_units <= 0
            or income_row.fair_value_usd != income_row.quantity * income_row.price_usd
        ):
            raise ReconciliationError(
                f"Income {income_row.income_id} does not reconcile quantity, price, and value."
            )
        created_lot = lots_by_id.get(income_row.created_lot_id)
        if (
            created_lot is None
            or created_lot.source_event_id != income_row.event_id
            or created_lot.wallet_id != income_row.wallet_id
            or created_lot.mint != income_row.mint
            or created_lot.starting_quantity_base_units != income_row.quantity_base_units
            or not created_lot.starting_basis.is_known
            or created_lot.starting_basis.require_total_usd()
            != income_row.fair_value_usd
        ):
            raise ReconciliationError(
                f"Income {income_row.income_id} does not match its created-lot basis."
            )

    return ReconciliationResult(
        lot_count=len(lots),
        consumption_count=len(consumptions),
        movement_count=len(movements),
        disposal_count=len(disposals),
        income_count=len(income),
    )
