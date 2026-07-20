"""Validate and collect structured business exceptions in stable order."""

from __future__ import annotations

from engine.core.identity import stable_id
from engine.models import (
    DomainException,
    ExceptionSeverity,
    ObservedFact,
    PipelineStage,
    SourceReference,
)


class ExceptionContractError(ValueError):
    """Raised when an exception omits evidence needed for human follow-up."""

class ExceptionCollector:
    """Own exception creation so every pipeline stage emits the same contract."""

    def __init__(self) -> None:
        self._items: list[DomainException] = []

    def add(
        self,
        *,
        code: str,
        severity: ExceptionSeverity,
        stage: PipelineStage,
        source_refs: tuple[SourceReference, ...],
        observed_facts: tuple[ObservedFact, ...],
        reason: str,
        affected_outputs: tuple[str, ...],
        related_object_ids: tuple[str, ...],
        human_request: str,
    ) -> DomainException:
        """Validate, identify, and store one actionable exception."""
        required_text = {
            "code": code,
            "reason": reason,
            "human_request": human_request,
        }
        missing = tuple(name for name, value in required_text.items() if not value.strip())
        if missing:
            raise ExceptionContractError(f"Missing exception text field(s): {', '.join(missing)}.")
        if not source_refs:
            raise ExceptionContractError("An exception must reference at least one source record.")
        if not observed_facts:
            raise ExceptionContractError("An exception must record at least one observed fact.")
        if not affected_outputs:
            raise ExceptionContractError("An exception must identify at least one affected output.")

        source_ids = tuple(sorted(source.source_id for source in source_refs))
        exception = DomainException(
            exception_id=stable_id(
                "exc", code, stage.value, *source_ids, *sorted(related_object_ids)
            ),
            code=code,
            severity=severity,
            stage=stage,
            source_refs=tuple(sorted(source_refs, key=lambda source: source.source_id)),
            observed_facts=tuple(sorted(observed_facts, key=lambda fact: (fact.name, fact.value))),
            reason=reason,
            affected_outputs=tuple(sorted(affected_outputs)),
            related_object_ids=tuple(sorted(related_object_ids)),
            human_request=human_request,
        )
        self._items.append(exception)
        return exception

    def items(self) -> tuple[DomainException, ...]:
        """Return exceptions by stable ID for deterministic output."""
        return tuple(sorted(self._items, key=lambda item: item.exception_id))
