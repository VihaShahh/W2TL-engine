"""Stable identity checks."""

import pytest

from engine.core.identity import derive_event_id, derive_source_id, stable_id


def test_equivalent_source_facts_produce_identical_ids() -> None:
    def derive_example_source() -> str:
        return derive_source_id(
            fixture_set_id="final_wallet_history",
            wallet_id="synthetic_wallet_alpha",
            fixture_record_id="final-buy-001",
            signature="synthetic_signature_buy_001",
            slot=100,
            instruction_index=0,
        )

    assert derive_example_source() == derive_example_source()

def test_changed_source_fact_changes_downstream_event_id() -> None:
    first_source = derive_source_id(
        fixture_set_id="final_wallet_history",
        wallet_id="synthetic_wallet_alpha",
        fixture_record_id="final-buy-001",
        signature="synthetic_signature_buy_001",
        slot=100,
        instruction_index=0,
    )
    second_source = derive_source_id(
        fixture_set_id="final_wallet_history",
        wallet_id="synthetic_wallet_alpha",
        fixture_record_id="final-buy-001",
        signature="synthetic_signature_buy_001",
        slot=101,
        instruction_index=0,
    )

    assert first_source != second_source
    assert derive_event_id(source_id=first_source) != derive_event_id(source_id=second_source)

def test_boolean_stable_fact_is_rejected() -> None:
    with pytest.raises(ValueError, match="Boolean"):
        stable_id("evt", True)
