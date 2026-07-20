"""Prove that every stored record reaches exactly one terminal evidence state."""

from __future__ import annotations

from engine.models import (
    Classification,
    CoverageEntry,
    CoverageStatus,
    DomainException,
    NormalizedEvent,
    ResolutionState,
    SourceReference,
)


def build_coverage(
    *,
    sources: tuple[SourceReference, ...],
    events: tuple[NormalizedEvent, ...],
    classifications: tuple[Classification, ...],
    exceptions: tuple[DomainException, ...],
    quarantined_source_ids: frozenset[str],
    secondary_source_ids: frozenset[str],
    review_flagged_source_ids: frozenset[str],
) -> tuple[CoverageEntry, ...]:
    """Assign one terminal state to each source record or fail the invariant."""
    event_by_source = {
        source.source_id: event
        for event in events
        for source in event.source_refs
    }
    classification_by_event = {item.event_id: item for item in classifications}
    exception_ids_by_source: dict[str, list[str]] = {}
    for exception in exceptions:
        for source in exception.source_refs:
            exception_ids_by_source.setdefault(source.source_id, []).append(
                exception.exception_id
            )

    entries: list[CoverageEntry] = []
    for source in sorted(sources, key=lambda item: item.source_id):
        event = event_by_source.get(source.source_id)
        if source.source_id in quarantined_source_ids:
            status = CoverageStatus.QUARANTINED
        elif source.source_id in secondary_source_ids:
            status = CoverageStatus.SECONDARY_VIEW
        elif source.source_id in review_flagged_source_ids:
            status = CoverageStatus.REVIEW_FLAGGED
        elif event is None:
            raise ValueError(f"Source {source.source_id} has no terminal coverage state.")
        else:
            classification = classification_by_event[event.event_id]
            status = (
                CoverageStatus.RESOLVED
                if classification.state is ResolutionState.RESOLVED
                else CoverageStatus.REVIEW_FLAGGED
            )
        entries.append(
            CoverageEntry(
                source=source,
                status=status,
                event_id=event.event_id if event is not None else None,
                exception_ids=tuple(sorted(exception_ids_by_source.get(source.source_id, []))),
            )
        )
    if len(entries) != len(sources) or len({item.source.source_id for item in entries}) != len(
        sources
    ):
        raise ValueError("Coverage must contain each source record exactly once.")
    return tuple(entries)
