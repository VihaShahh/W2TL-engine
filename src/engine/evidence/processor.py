"""Run neutral evidence processing before tax-lot accounting begins."""

from __future__ import annotations

from dataclasses import dataclass

from engine.core.exceptions import ExceptionCollector
from engine.evidence.classifier import classify_event
from engine.evidence.coverage import build_coverage
from engine.evidence.duplicates import resolve_duplicates
from engine.evidence.normalizer import normalize_fixture
from engine.evidence.transfers import match_owned_transfers
from engine.models import (
    Classification,
    ClassificationType,
    CoverageEntry,
    DomainException,
    ExceptionSeverity,
    FixtureBundle,
    NormalizedEvent,
    ObservedFact,
    PipelineStage,
    ResolutionState,
)


@dataclass(frozen=True, slots=True)
class EvidenceResult:
    events: tuple[NormalizedEvent, ...]
    classifications: tuple[Classification, ...]
    coverage: tuple[CoverageEntry, ...]
    exceptions: tuple[DomainException, ...]

def _unique_exceptions(*groups: tuple[DomainException, ...]) -> tuple[DomainException, ...]:
    by_id = {item.exception_id: item for group in groups for item in group}
    return tuple(sorted(by_id.values(), key=lambda item: item.exception_id))

def process_evidence(bundle: FixtureBundle) -> EvidenceResult:
    """Normalize, deduplicate, match transfers, classify, and close source coverage."""
    normalized = normalize_fixture(bundle)
    duplicates = resolve_duplicates(normalized.events)
    transfers = match_owned_transfers(
        duplicates.events, owned_wallet_ids=bundle.owned_wallet_ids
    )

    collector = ExceptionCollector()
    classifications: list[Classification] = []
    for event in transfers.events:
        event_source_ids = {source.source_id for source in event.source_refs}
        if event_source_ids & transfers.review_flagged_source_ids:
            classification = Classification(
                event_id=event.event_id,
                type=ClassificationType.INTERNAL_TRANSFER,
                state=ResolutionState.REVIEW_FLAGGED,
                rule_id="core.owned_transfer.incomplete",
                evidence_source_ids=tuple(sorted(event_source_ids)),
            )
        else:
            classification = classify_event(event)
        classifications.append(classification)
        if classification.state is not ResolutionState.RESOLVED and not (
            classification.type is ClassificationType.SPAM_AIRDROP
            and classification.state is ResolutionState.REVIEW_FLAGGED
        ):
            collector.add(
                code="review_flagged_classification",
                severity=ExceptionSeverity.EVENT_BLOCKING,
                stage=PipelineStage.CLASSIFICATION,
                source_refs=event.source_refs,
                observed_facts=(
                    ObservedFact(name="rule_id", value=classification.rule_id),
                    ObservedFact(name="type", value=classification.type.value),
                ),
                reason="The evidence does not support complete automatic tax treatment.",
                affected_outputs=("disposals.csv", "income.csv", "exceptions.json"),
                related_object_ids=(event.event_id,),
                human_request="Review the named rule and provide the missing event evidence.",
            )

    all_exceptions = _unique_exceptions(
        bundle.load_exceptions,
        normalized.exceptions,
        duplicates.exceptions,
        transfers.exceptions,
        collector.items(),
    )
    classified = tuple(classifications)
    coverage = build_coverage(
        sources=bundle.all_source_refs,
        events=transfers.events,
        classifications=classified,
        exceptions=all_exceptions,
        quarantined_source_ids=frozenset(
            {
                *(
                    source.source_id
                    for item in bundle.load_exceptions
                    for source in item.source_refs
                ),
                *normalized.quarantined_source_ids,
                *duplicates.quarantined_source_ids,
            }
        ),
        secondary_source_ids=frozenset(
            {*duplicates.secondary_source_ids, *transfers.secondary_source_ids}
        ),
        review_flagged_source_ids=transfers.review_flagged_source_ids,
    )
    return EvidenceResult(
        events=transfers.events,
        classifications=classified,
        coverage=coverage,
        exceptions=all_exceptions,
    )
