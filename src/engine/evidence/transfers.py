"""Match the two owned-wallet views of one non-taxable movement."""

from __future__ import annotations

from dataclasses import dataclass, replace

from engine.core.exceptions import ExceptionCollector
from engine.core.identity import stable_id
from engine.evidence.normalizer import event_order_key
from engine.models import (
    Direction,
    DomainException,
    ExceptionSeverity,
    NormalizedEvent,
    ObservedFact,
    PipelineStage,
)


@dataclass(frozen=True, slots=True)
class TransferResolution:
    events: tuple[NormalizedEvent, ...]
    secondary_source_ids: frozenset[str]
    review_flagged_source_ids: frozenset[str]
    exceptions: tuple[DomainException, ...]

def _is_compatible_pair(
    first: NormalizedEvent,
    second: NormalizedEvent,
    *,
    owned_wallet_ids: frozenset[str],
) -> bool:
    if first.wallet_id == second.wallet_id:
        return False
    if {first.wallet_id, second.wallet_id} - owned_wallet_ids:
        return False
    if len(first.token_legs) != 1 or len(second.token_legs) != 1:
        return False
    first_leg = first.token_legs[0]
    second_leg = second.token_legs[0]
    return (
        first.timestamp_utc == second.timestamp_utc
        and first.status == second.status
        and first_leg.mint == second_leg.mint
        and first_leg.quantity_base_units == second_leg.quantity_base_units
        and first_leg.direction is not second_leg.direction
        and first.counterparty_wallet_id == second.wallet_id
        and second.counterparty_wallet_id == first.wallet_id
    )

def match_owned_transfers(
    events: tuple[NormalizedEvent, ...], *, owned_wallet_ids: tuple[str, ...]
) -> TransferResolution:
    """Combine an exact outbound/inbound pair and flag uncertain owned movement."""
    owned = frozenset(owned_wallet_ids)
    candidates: dict[str, list[NormalizedEvent]] = {}
    ordinary: list[NormalizedEvent] = []
    for event in events:
        is_owned_candidate = (
            event.instruction_type == "transfer"
            and event.counterparty_wallet_id in owned
            and event.wallet_id in owned
        )
        if is_owned_candidate:
            candidates.setdefault(event.source_refs[0].signature, []).append(event)
        else:
            ordinary.append(event)

    collector = ExceptionCollector()
    combined: list[NormalizedEvent] = ordinary
    secondary: set[str] = set()
    review_flagged: set[str] = set()
    for signature, group in candidates.items():
        ordered = sorted(group, key=event_order_key)
        if len(ordered) == 2 and _is_compatible_pair(
            ordered[0], ordered[1], owned_wallet_ids=owned
        ):
            outbound = next(
                event
                for event in ordered
                if event.token_legs[0].direction is Direction.OUTBOUND
            )
            inbound = next(event for event in ordered if event is not outbound)
            source_refs = tuple(
                sorted(
                    (*outbound.source_refs, *inbound.source_refs),
                    key=lambda source: source.source_id,
                )
            )
            event_id = stable_id(
                "evt", "owned_transfer", *(source.source_id for source in source_refs)
            )
            combined.append(
                replace(
                    outbound,
                    event_id=event_id,
                    source_refs=source_refs,
                    instruction_type="internal_transfer",
                    counterparty_wallet_id=inbound.wallet_id,
                )
            )
            secondary.update(source.source_id for source in inbound.source_refs)
            continue

        source_refs = tuple(
            sorted(
                (source for event in ordered for source in event.source_refs),
                key=lambda source: source.source_id,
            )
        )
        review_flagged.update(source.source_id for source in source_refs)
        combined.extend(ordered)
        collector.add(
            code="unmatched_owned_transfer",
            severity=ExceptionSeverity.EVENT_BLOCKING,
            stage=PipelineStage.CLASSIFICATION,
            source_refs=source_refs,
            observed_facts=(
                ObservedFact(name="signature", value=signature),
                ObservedFact(name="candidate_count", value=str(len(ordered))),
            ),
            reason="Owned-wallet transfer evidence is incomplete, ambiguous, or contradictory.",
            affected_outputs=("disposals.csv", "income.csv", "exceptions.json"),
            related_object_ids=tuple(event.event_id for event in ordered),
            human_request="Provide both owned-wallet views with matching time, mint, and quantity.",
        )
    return TransferResolution(
        events=tuple(sorted(combined, key=event_order_key)),
        secondary_source_ids=frozenset(secondary),
        review_flagged_source_ids=frozenset(review_flagged),
        exceptions=collector.items(),
    )
