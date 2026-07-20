"""Resolve repeated wallet views before accounting can double count them."""

from __future__ import annotations

from dataclasses import dataclass, replace

from engine.core.exceptions import ExceptionCollector
from engine.core.identity import stable_id
from engine.evidence.normalizer import event_order_key
from engine.models import (
    DomainException,
    ExceptionSeverity,
    NormalizedEvent,
    ObservedFact,
    PipelineStage,
)


@dataclass(frozen=True, slots=True)
class DuplicateResolution:
    events: tuple[NormalizedEvent, ...]
    secondary_source_ids: frozenset[str]
    quarantined_source_ids: frozenset[str]
    exceptions: tuple[DomainException, ...]

def _facts(event: NormalizedEvent) -> tuple[object, ...]:
    return (
        event.timestamp_utc,
        event.slot,
        event.instruction_index,
        event.wallet_id,
        event.status,
        event.program,
        event.instruction_type,
        tuple(
            (
                leg.mint,
                leg.symbol,
                leg.decimals,
                leg.direction,
                leg.quantity_base_units,
            )
            for leg in event.token_legs
        ),
        event.fee,
        event.cash_value_usd,
        event.counterparty_wallet_id,
    )

def resolve_duplicates(events: tuple[NormalizedEvent, ...]) -> DuplicateResolution:
    """Keep one identical same-wallet view and quarantine contradictory repetitions."""
    groups: dict[tuple[str, str, int], list[NormalizedEvent]] = {}
    for event in events:
        source = event.source_refs[0]
        groups.setdefault(
            (event.wallet_id, source.signature, event.instruction_index), []
        ).append(event)

    collector = ExceptionCollector()
    resolved: list[NormalizedEvent] = []
    secondary: set[str] = set()
    quarantined: set[str] = set()
    for group in groups.values():
        ordered = sorted(group, key=event_order_key)
        if len(ordered) == 1:
            resolved.append(ordered[0])
            continue
        source_refs = tuple(
            sorted(
                (source for event in ordered for source in event.source_refs),
                key=lambda source: source.source_id,
            )
        )
        if len({_facts(event) for event in ordered}) != 1:
            quarantined.update(source.source_id for source in source_refs)
            collector.add(
                code="conflicting_duplicate_records",
                severity=ExceptionSeverity.EVENT_BLOCKING,
                stage=PipelineStage.NORMALIZATION,
                source_refs=source_refs,
                observed_facts=(
                    ObservedFact(name="signature", value=source_refs[0].signature),
                    ObservedFact(name="record_count", value=str(len(source_refs))),
                ),
                reason="The same wallet and signature contain contradictory economic facts.",
                affected_outputs=("disposals.csv", "income.csv", "exceptions.json"),
                related_object_ids=(),
                human_request="Identify the authoritative wallet record and correct the conflict.",
            )
            continue

        primary = ordered[0]
        primary_source_id = primary.source_refs[0].source_id
        secondary.update(
            source.source_id for source in source_refs if source.source_id != primary_source_id
        )
        event_id = stable_id(
            "evt", "duplicate", *(source.source_id for source in source_refs)
        )
        resolved.append(replace(primary, event_id=event_id, source_refs=source_refs))
        collector.add(
            code="identical_duplicate_record",
            severity=ExceptionSeverity.WARNING,
            stage=PipelineStage.NORMALIZATION,
            source_refs=source_refs,
            observed_facts=(
                ObservedFact(name="signature", value=source_refs[0].signature),
                ObservedFact(name="record_count", value=str(len(source_refs))),
            ),
            reason="Equivalent same-wallet records represent one economic event.",
            affected_outputs=("exceptions.json",),
            related_object_ids=(event_id,),
            human_request="No action is required unless the duplicate source was unexpected.",
        )
    return DuplicateResolution(
        events=tuple(sorted(resolved, key=event_order_key)),
        secondary_source_ids=frozenset(secondary),
        quarantined_source_ids=frozenset(quarantined),
        exceptions=collector.items(),
    )
