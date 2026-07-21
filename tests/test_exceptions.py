"""Structured exception contract checks."""

import pytest

from engine.core.exceptions import ExceptionCollector, ExceptionContractError
from engine.models import (
    ExceptionSeverity,
    ObservedFact,
    PipelineStage,
    SourceReference,
)


def source_ref() -> SourceReference:
    return SourceReference(
        source_id="src_example",
        fixture_set_id="final_wallet_history",
        wallet_id="synthetic_wallet_alpha",
        fixture_record_id="final-buy-001",
        signature="synthetic_signature_buy_001",
    )

def add_complete_exception(collector: ExceptionCollector, *, reason: str = "Missing basis.") -> str:
    item = collector.add(
        code="unknown_basis",
        severity=ExceptionSeverity.MONETARY_OUTPUT_BLOCKING,
        stage=PipelineStage.ACCOUNTING,
        source_refs=(source_ref(),),
        observed_facts=(ObservedFact(name="quantity_base_units", value="1000000"),),
        reason=reason,
        affected_outputs=("disposals.csv",),
        related_object_ids=("evt_example",),
        human_request="Provide the original acquisition date and total basis.",
    )
    return item.exception_id

def test_equivalent_exception_facts_produce_stable_id() -> None:
    first_id = add_complete_exception(ExceptionCollector())
    second_id = add_complete_exception(ExceptionCollector())

    assert first_id == second_id

def test_exception_collector_sorts_output_by_stable_id() -> None:
    collector = ExceptionCollector()
    add_complete_exception(collector)

    assert collector.items()[0].code == "unknown_basis"

@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("reason", "", "Missing exception text field"),
        ("source_refs", (), "at least one source"),
        ("observed_facts", (), "at least one observed fact"),
        ("affected_outputs", (), "at least one affected output"),
    ],
)
def test_required_exception_fields_are_enforced(
    field: str, value: object, message: str
) -> None:
    arguments: dict[str, object] = {
        "code": "unknown_basis",
        "severity": ExceptionSeverity.MONETARY_OUTPUT_BLOCKING,
        "stage": PipelineStage.ACCOUNTING,
        "source_refs": (source_ref(),),
        "observed_facts": (ObservedFact(name="quantity", value="1"),),
        "reason": "Missing basis.",
        "affected_outputs": ("disposals.csv",),
        "related_object_ids": ("evt_example",),
        "human_request": "Provide basis evidence.",
    }
    arguments[field] = value

    with pytest.raises(ExceptionContractError, match=message):
        ExceptionCollector().add(**arguments)  # type: ignore[arg-type]
