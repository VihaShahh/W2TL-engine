"""Actionable review contracts for unresolved domain evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.models.states import ExceptionSeverity, PipelineStage

if TYPE_CHECKING:
    from engine.models.evidence import SourceReference

@dataclass(frozen=True, slots=True)
class ObservedFact:
    """One human-readable fact retained inside an actionable exception.

    Name/value pairs explain what the engine actually observed without turning that observation
    into an unsupported classification or amount.
    """

    name: str
    value: str

@dataclass(frozen=True, slots=True)
class DomainException:
    """Actionable uncertainty record that can safely coexist with valid events.

    Source lineage, observed facts, impact, reason, and a human request make the object suitable
    for reviewer workflow rather than a developer-only warning log.
    """

    exception_id: str
    code: str
    severity: ExceptionSeverity
    stage: PipelineStage
    source_refs: tuple[SourceReference, ...]
    observed_facts: tuple[ObservedFact, ...]
    reason: str
    affected_outputs: tuple[str, ...]
    related_object_ids: tuple[str, ...]
    human_request: str
