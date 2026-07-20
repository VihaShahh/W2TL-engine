"""Stable external-contract and assessment-policy selections."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

CONTRACT_VERSION = 1

class UnsupportedContractError(ValueError):
    """Raised when a fixture uses a contract this engine cannot read."""

def require_supported_contract(*, actual: object, name: str) -> None:
    if actual != CONTRACT_VERSION:
        raise UnsupportedContractError(
            f"Unsupported {name} contract {actual!r}; expected {CONTRACT_VERSION}."
        )

class UnsupportedPolicyError(ValueError):
    """Raised when a run requests a policy ID or version this engine does not ship."""

class Jurisdiction(StrEnum):
    US = "US"

class ValuationCurrency(StrEnum):
    USD = "USD"

class BasisMethod(StrEnum):
    FIFO = "FIFO"

class BasisScope(StrEnum):
    WALLET_AND_MINT = "wallet_and_mint"

class PriceRule(StrEnum):
    EXACT_TIMESTAMP = "exact_timestamp"

class SwapValuationRule(StrEnum):
    REQUIRE_AGREEING_LEGS = "require_agreeing_legs"

class HoldingTermRule(StrEnum):
    MORE_THAN_ONE_YEAR = "more_than_one_year"

class PartialOutputRule(StrEnum):
    REPORT_COMPLETE_SLICES = "report_complete_slices"

class OutboundRule(StrEnum):
    SUSPENSE = "suspense"

class SpamRule(StrEnum):
    QUARANTINE_UNPRICED = "quarantine_unpriced"

class FeeRule(StrEnum):
    EVENT_CONTEXT = "event_context"

class RoundingRule(StrEnum):
    HALF_EVEN = "half_even"

@dataclass(frozen=True, slots=True)
class AssessmentPolicy:
    """One complete immutable selection of supported assessment behavior."""

    policy_id: str
    policy_version: int
    jurisdiction: Jurisdiction
    valuation_currency: ValuationCurrency
    basis_method: BasisMethod
    basis_scope: BasisScope
    price_rule: PriceRule
    swap_valuation_rule: SwapValuationRule
    holding_term_rule: HoldingTermRule
    partial_output_rule: PartialOutputRule
    outbound_rule: OutboundRule
    spam_rule: SpamRule
    fee_rule: FeeRule
    rounding_rule: RoundingRule
    report_decimal_places: int

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("Assessment policy ID must not be empty.")
        if (
            isinstance(self.policy_version, bool)
            or not isinstance(self.policy_version, int)
            or self.policy_version < 1
        ):
            raise ValueError("Assessment policy version must be a positive integer.")
        selections = (
            ("jurisdiction", self.jurisdiction, Jurisdiction),
            ("valuation_currency", self.valuation_currency, ValuationCurrency),
            ("basis_method", self.basis_method, BasisMethod),
            ("basis_scope", self.basis_scope, BasisScope),
            ("price_rule", self.price_rule, PriceRule),
            ("swap_valuation_rule", self.swap_valuation_rule, SwapValuationRule),
            ("holding_term_rule", self.holding_term_rule, HoldingTermRule),
            ("partial_output_rule", self.partial_output_rule, PartialOutputRule),
            ("outbound_rule", self.outbound_rule, OutboundRule),
            ("spam_rule", self.spam_rule, SpamRule),
            ("fee_rule", self.fee_rule, FeeRule),
            ("rounding_rule", self.rounding_rule, RoundingRule),
        )
        for name, value, selection_type in selections:
            if not isinstance(value, selection_type):
                raise ValueError(f"Assessment policy {name} must be a supported selection.")
        if (
            isinstance(self.report_decimal_places, bool)
            or not isinstance(self.report_decimal_places, int)
            or self.report_decimal_places < 0
        ):
            raise ValueError("Report decimal places must be a non-negative integer.")

    @property
    def key(self) -> tuple[str, int]:
        return self.policy_id, self.policy_version

    def metadata(self) -> dict[str, str | int]:
        """Return the stable policy fields written with generated output."""
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "jurisdiction": self.jurisdiction.value,
            "valuation_currency": self.valuation_currency.value,
            "basis_method": self.basis_method.value,
            "basis_scope": self.basis_scope.value,
            "price_rule": self.price_rule.value,
            "swap_valuation_rule": self.swap_valuation_rule.value,
            "holding_term_rule": self.holding_term_rule.value,
            "partial_output_rule": self.partial_output_rule.value,
            "outbound_rule": self.outbound_rule.value,
            "spam_rule": self.spam_rule.value,
            "fee_rule": self.fee_rule.value,
            "rounding_rule": self.rounding_rule.value,
            "report_decimal_places": self.report_decimal_places,
        }

US_FIFO_POLICY = AssessmentPolicy(
    policy_id="us_federal_assessment_fifo",
    policy_version=1,
    jurisdiction=Jurisdiction.US,
    valuation_currency=ValuationCurrency.USD,
    basis_method=BasisMethod.FIFO,
    basis_scope=BasisScope.WALLET_AND_MINT,
    price_rule=PriceRule.EXACT_TIMESTAMP,
    swap_valuation_rule=SwapValuationRule.REQUIRE_AGREEING_LEGS,
    holding_term_rule=HoldingTermRule.MORE_THAN_ONE_YEAR,
    partial_output_rule=PartialOutputRule.REPORT_COMPLETE_SLICES,
    outbound_rule=OutboundRule.SUSPENSE,
    spam_rule=SpamRule.QUARANTINE_UNPRICED,
    fee_rule=FeeRule.EVENT_CONTEXT,
    rounding_rule=RoundingRule.HALF_EVEN,
    report_decimal_places=2,
)

SUPPORTED_POLICIES = (US_FIFO_POLICY,)

def require_supported_policy(policy: AssessmentPolicy) -> None:
    """Reject modified definitions even when they reuse a supported policy key."""
    if policy not in SUPPORTED_POLICIES:
        raise UnsupportedPolicyError(
            f"Unsupported assessment policy definition for {policy.policy_id!r} "
            f"version {policy.policy_version!r}."
        )

def select_policy(*, policy_id: str, policy_version: int) -> AssessmentPolicy:
    """Select one exact supported policy key without version or jurisdiction fallback."""
    if (
        not isinstance(policy_id, str)
        or not policy_id
        or isinstance(policy_version, bool)
        or not isinstance(policy_version, int)
    ):
        raise UnsupportedPolicyError(
            f"Unsupported assessment policy {policy_id!r} version {policy_version!r}."
        )
    for policy in SUPPORTED_POLICIES:
        if policy.key == (policy_id, policy_version):
            return policy
    raise UnsupportedPolicyError(
        f"Unsupported assessment policy {policy_id!r} version {policy_version!r}."
    )
