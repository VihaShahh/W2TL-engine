"""Apply basis continuity only to explicitly supported one-to-one wraps."""

from __future__ import annotations

from engine.accounting.lots import TaxLotLedger
from engine.core.exceptions import ExceptionCollector
from engine.models import (
    Acquisition,
    Direction,
    ExceptionSeverity,
    InventoryState,
    NormalizedEvent,
    ObservedFact,
    PipelineStage,
    TokenLeg,
    Wrap,
)

SUPPORTED_WRAP_PAIRS = frozenset(
    {("synthetic_mint_skito", "synthetic_mint_wrapped_skito")}
)

def _legs(event: NormalizedEvent) -> tuple[TokenLeg, TokenLeg]:
    outbound = tuple(
        leg for leg in event.token_legs if leg.direction is Direction.OUTBOUND
    )
    inbound = tuple(
        leg for leg in event.token_legs if leg.direction is Direction.INBOUND
    )
    if len(outbound) != 1 or len(inbound) != 1:
        raise ValueError(f"Wrap event {event.event_id} must have one leg in each direction.")
    return outbound[0], inbound[0]

def account_wrap(
    *,
    event: NormalizedEvent,
    ledger: TaxLotLedger,
    collector: ExceptionCollector,
) -> Wrap:
    """Carry supported continuity or preserve both sides as unresolved inventory."""
    source_leg, destination_leg = _legs(event)
    pair = (source_leg.mint, destination_leg.mint)
    is_supported = (
        event.program == "synthetic_wrap_program"
        and pair in SUPPORTED_WRAP_PAIRS
        and source_leg.decimals == destination_leg.decimals
        and source_leg.quantity_base_units == destination_leg.quantity_base_units
        and not event.fee.charged
    )
    if is_supported:
        movements = ledger.convert(
            event,
            source_leg=source_leg,
            destination_leg=destination_leg,
        )
        return Wrap(
            event_id=event.event_id,
            source_leg=source_leg,
            destination_leg=destination_leg,
            movements=movements,
            destination_lots=tuple(
                ledger.get_lot(item.destination_lot_id) for item in movements
            ),
        )

    exception = collector.add(
        code="ambiguous_wrap_treatment",
        severity=ExceptionSeverity.MONETARY_OUTPUT_BLOCKING,
        stage=PipelineStage.ACCOUNTING,
        source_refs=event.source_refs,
        observed_facts=(
            ObservedFact(name="program", value=event.program),
            ObservedFact(name="source_mint", value=source_leg.mint),
            ObservedFact(name="destination_mint", value=destination_leg.mint),
            ObservedFact(
                name="source_quantity_base_units",
                value=str(source_leg.quantity_base_units),
            ),
            ObservedFact(
                name="destination_quantity_base_units",
                value=str(destination_leg.quantity_base_units),
            ),
            ObservedFact(name="fee_charged", value=str(event.fee.charged).lower()),
        ),
        reason=(
            "The evidence does not prove a supported same-owner one-to-one token "
            "transformation."
        ),
        affected_outputs=("disposals.csv", "income.csv"),
        related_object_ids=(event.event_id, source_leg.component_id, destination_leg.component_id),
        human_request=(
            "Provide the wrap program, token-pair mapping, exact one-to-one quantity, "
            "beneficial ownership, and fee treatment evidence."
        ),
    )
    movements = ledger.suspend_leg(event, leg=source_leg)
    destination_lot = ledger.acquire_unknown(
        event,
        leg=destination_leg,
        exception_id=exception.exception_id,
        acquisition=Acquisition.unknown(
            observed_at_utc=event.timestamp_utc,
            exception_id=exception.exception_id,
        ),
        state=InventoryState.QUARANTINED,
    )
    return Wrap(
        event_id=event.event_id,
        source_leg=source_leg,
        destination_leg=destination_leg,
        movements=movements,
        destination_lots=(destination_lot,),
        exception_id=exception.exception_id,
    )
