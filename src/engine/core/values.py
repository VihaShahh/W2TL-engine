"""Exact accounting input helpers that reject binary floating-point values."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum


class ExactValueError(ValueError):
    """Raised when an accounting value is inexact, malformed, or out of range."""

def parse_base_units(value: object, *, field_name: str, allow_negative: bool = False) -> int:
    """Return an integer base-unit amount without coercing booleans or floats."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExactValueError(f"{field_name} must be an integer base-unit amount, got {value!r}.")
    if not allow_negative and value < 0:
        raise ExactValueError(f"{field_name} must not be negative, got {value}.")
    return value

def parse_decimal_string(value: object, *, field_name: str) -> Decimal:
    """Parse a finite decimal string without accepting integers or binary floats."""
    if not isinstance(value, str) or not value.strip():
        raise ExactValueError(f"{field_name} must be a non-empty decimal string, got {value!r}.")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ExactValueError(f"{field_name} is not a valid decimal string: {value!r}.") from error
    if not parsed.is_finite():
        raise ExactValueError(f"{field_name} must be finite, got {value!r}.")
    return parsed

def base_units_to_decimal(quantity_base_units: int, *, decimals: int) -> Decimal:
    """Convert base units exactly, such as 1,500,000 units to 1.5 at six decimals."""
    quantity = parse_base_units(
        quantity_base_units, field_name="quantity_base_units", allow_negative=True
    )
    if isinstance(decimals, bool) or not isinstance(decimals, int) or decimals < 0:
        raise ExactValueError(f"decimals must be a non-negative integer, got {decimals!r}.")
    return Decimal(quantity).scaleb(-decimals)

def assert_no_binary_floats(value: object, *, path: str = "root") -> None:
    """Walk nested models and fail at the exact path of any binary float."""
    if isinstance(value, float):
        raise ExactValueError(f"Binary float found at {path}: {value!r}.")
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            assert_no_binary_floats(getattr(value, field.name), path=f"{path}.{field.name}")
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            assert_no_binary_floats(nested, path=f"{path}[{key!r}]")
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        for index, nested in enumerate(value):
            assert_no_binary_floats(nested, path=f"{path}[{index}]")
        return
    if isinstance(value, Enum):
        assert_no_binary_floats(value.value, path=f"{path}.value")
