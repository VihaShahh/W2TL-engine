"""Exact-value parsing and no-float traversal checks."""

from decimal import Decimal

import pytest

from engine.core.values import (
    ExactValueError,
    assert_no_binary_floats,
    base_units_to_decimal,
    parse_base_units,
    parse_decimal_string,
)


def test_base_units_convert_exactly() -> None:
    assert parse_base_units(1_500_000, field_name="quantity") == 1_500_000
    assert base_units_to_decimal(1_500_000, decimals=6) == Decimal("1.5")

@pytest.mark.parametrize("value", [True, False, 1.0, "1", None])
def test_base_units_reject_non_integers(value: object) -> None:
    with pytest.raises(ExactValueError, match="integer base-unit amount"):
        parse_base_units(value, field_name="quantity")

@pytest.mark.parametrize("value", [1.25, 1, True, "NaN", "Infinity", "", "wrong"])
def test_decimal_parser_rejects_inexact_or_malformed_input(value: object) -> None:
    with pytest.raises(ExactValueError):
        parse_decimal_string(value, field_name="price_usd")

def test_decimal_string_is_parsed_without_float_artifacts() -> None:
    assert parse_decimal_string("0.100000000000000001", field_name="price_usd") == Decimal(
        "0.100000000000000001"
    )

def test_model_traversal_reports_float_path() -> None:
    with pytest.raises(ExactValueError, match=r"root\['nested'\]\[1\]"):
        assert_no_binary_floats({"nested": [Decimal("1"), 2.5]})

def test_model_traversal_accepts_exact_values() -> None:
    assert_no_binary_floats({"quantity": 2_000_000, "money": Decimal("8.00")})
