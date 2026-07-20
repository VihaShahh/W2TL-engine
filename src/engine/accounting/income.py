"""Account for valued and unresolved reward or airdrop receipts."""

from __future__ import annotations

from engine.accounting.lots import TaxLotLedger
from engine.core.exceptions import ExceptionCollector
from engine.core.values import base_units_to_decimal
from engine.models import (
    ClassificationType,
    Direction,
    ExceptionSeverity,
    Income,
    InventoryState,
    NormalizedEvent,
    ObservedFact,
    PipelineStage,
    PricePoint,
    PriceResolution,
    PriceResolutionState,
    TokenLeg,
)
from engine.policy.config import AssessmentPolicy, SpamRule
from engine.policy.pricing import resolve_price

INCOME_TYPES = frozenset(
    {
        ClassificationType.STAKING_REWARD,
        ClassificationType.AIRDROP,
        ClassificationType.SPAM_AIRDROP,
    }
)

def _receipt_leg(event: NormalizedEvent) -> TokenLeg:
    if len(event.token_legs) != 1 or event.token_legs[0].direction is not Direction.INBOUND:
        raise ValueError(f"Income event {event.event_id} must have one inbound token leg.")
    return event.token_legs[0]

def _price_facts(resolution: PriceResolution) -> tuple[ObservedFact, ...]:
    return (
        ObservedFact(name="price_state", value=resolution.state.value),
        ObservedFact(
            name="price_source_ids",
            value=",".join(resolution.source_ids) or "none",
        ),
    )

def _preserve_unvalued_receipt(
    *,
    event: NormalizedEvent,
    leg: TokenLeg,
    resolution: PriceResolution,
    classification_type: ClassificationType,
    ledger: TaxLotLedger,
    collector: ExceptionCollector,
) -> None:
    is_spam = classification_type is ClassificationType.SPAM_AIRDROP
    if is_spam:
        if resolution.state is PriceResolutionState.MISSING:
            code = "unpriced_spam_airdrop"
            reason = "The spam-candidate receipt has no defensible exact value."
        elif resolution.state is PriceResolutionState.CONFLICT:
            code = "spam_airdrop_price_conflict"
            reason = "The spam-candidate receipt has conflicting exact price evidence."
        else:
            code = "spam_airdrop_requires_review"
            reason = "The receipt remains a spam candidate despite exact price evidence."
        human_request = (
            "Confirm whether the receipt is legitimate and provide classification and "
            "valuation evidence before recognizing income or opening spendable inventory."
        )
        state = InventoryState.QUARANTINED
    else:
        code = (
            "missing_income_price"
            if resolution.state is PriceResolutionState.MISSING
            else "conflicting_income_price"
        )
        reason = "The receipt quantity is known but its required exact value is unresolved."
        human_request = "Provide one exact timestamp USD price source for the received asset."
        state = InventoryState.OPEN

    exception = collector.add(
        code=code,
        severity=ExceptionSeverity.MONETARY_OUTPUT_BLOCKING,
        stage=PipelineStage.ACCOUNTING,
        source_refs=event.source_refs,
        observed_facts=(
            ObservedFact(name="category", value=classification_type.value),
            ObservedFact(name="mint", value=leg.mint),
            ObservedFact(
                name="quantity_base_units", value=str(leg.quantity_base_units)
            ),
            *_price_facts(resolution),
        ),
        reason=reason,
        affected_outputs=("income.csv",),
        related_object_ids=(
            event.event_id,
            leg.component_id,
            *resolution.source_ids,
        ),
        human_request=human_request,
    )
    ledger.acquire_unknown(
        event,
        leg=leg,
        exception_id=exception.exception_id,
        state=state,
    )

def account_income(
    *,
    event: NormalizedEvent,
    classification_type: ClassificationType,
    prices: tuple[PricePoint, ...],
    policy: AssessmentPolicy,
    ledger: TaxLotLedger,
    collector: ExceptionCollector,
) -> tuple[Income, ...]:
    """Create equal income and lot basis, or preserve unresolved receipt quantity."""
    if classification_type not in INCOME_TYPES:
        raise ValueError(f"Unsupported income classification: {classification_type.value}.")
    if (
        classification_type is ClassificationType.SPAM_AIRDROP
        and policy.spam_rule is not SpamRule.QUARANTINE_UNPRICED
    ):
        raise ValueError(f"Unsupported spam rule: {policy.spam_rule.value}.")

    leg = _receipt_leg(event)
    resolution = resolve_price(
        prices,
        mint=leg.mint,
        timestamp_utc=event.timestamp_utc,
        policy=policy,
    )
    if classification_type is ClassificationType.SPAM_AIRDROP or (
        resolution.state is not PriceResolutionState.FOUND
    ):
        _preserve_unvalued_receipt(
            event=event,
            leg=leg,
            resolution=resolution,
            classification_type=classification_type,
            ledger=ledger,
            collector=collector,
        )
        return ()

    quantity = base_units_to_decimal(
        leg.quantity_base_units, decimals=leg.decimals
    )
    price_usd = resolution.require_price_usd()
    fair_value_usd = quantity * price_usd
    lot = ledger.acquire(event, leg=leg, basis_usd=fair_value_usd)
    return (
        Income(
            event_id=event.event_id,
            source_id=event.source_refs[0].source_id,
            wallet_id=event.wallet_id,
            leg=leg,
            category=classification_type,
            quantity=quantity,
            received_at=event.timestamp_utc,
            price_usd=price_usd,
            price_source_id=resolution.require_source_id(),
            fair_value_usd=fair_value_usd,
            created_lot_id=lot.lot_id,
        ),
    )
