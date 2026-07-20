"""Actionable review contracts for unresolved domain evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.models.states import ExceptionSeverity, PipelineStage

if TYPE_CHECKING:
    from engine.models.evidence import SourceReference

@dataclass(frozen=True, slots=True)
class ObservedFact:
    name: str
    value: str

@dataclass(frozen=True, slots=True)
class DomainException:
    """Actionable uncertainty record that can safely coexist with valid events."""

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
