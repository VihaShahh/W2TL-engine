"""Value and account for both token legs of one supported swap."""

from __future__ import annotations

from decimal import Decimal

from engine.accounting.lots import TaxLotLedger
from engine.core.exceptions import ExceptionCollector
from engine.core.values import base_units_to_decimal
from engine.models import (
    Direction,
    EvidenceState,
    ExceptionSeverity,
    NormalizedEvent,
    ObservedFact,
    PipelineStage,
    PricePoint,
    PriceResolution,
    PriceResolutionState,
    Swap,
    TokenLeg,
)
from engine.policy.config import AssessmentPolicy, SwapValuationRule
from engine.policy.pricing import resolve_price


def _swap_legs(event: NormalizedEvent) -> tuple[TokenLeg, TokenLeg]:
    given = tuple(leg for leg in event.token_legs if leg.direction is Direction.OUTBOUND)
    received = tuple(leg for leg in event.token_legs if leg.direction is Direction.INBOUND)
    if len(given) != 1 or len(received) != 1 or len(event.token_legs) != 2:
        raise ValueError(
            f"Swap event {event.event_id} must contain one outbound and one inbound token leg."
        )
    return given[0], received[0]

def _implied_value(leg: TokenLeg, resolution: PriceResolution) -> Decimal:
    return base_units_to_decimal(
        leg.quantity_base_units, decimals=leg.decimals
    ) * resolution.require_price_usd()

def _resolution_facts(
    *, side: str, leg: TokenLeg, resolution: PriceResolution
) -> tuple[ObservedFact, ...]:
    facts = [
        ObservedFact(name=f"{side}_mint", value=leg.mint),
        ObservedFact(name=f"{side}_price_state", value=resolution.state.value),
        ObservedFact(
            name=f"{side}_price_source_ids",
            value=",".join(resolution.source_ids) or "none",
        ),
    ]
    if resolution.state is PriceResolutionState.FOUND:
        facts.append(
            ObservedFact(
                name=f"{side}_implied_value_usd",
                value=format(_implied_value(leg, resolution), "f"),
            )
        )
    return tuple(facts)

def account_swap(
    *,
    event: NormalizedEvent,
    prices: tuple[PricePoint, ...],
    policy: AssessmentPolicy,
    ledger: TaxLotLedger,
    collector: ExceptionCollector,
) -> Swap:
    """Consume the asset given and preserve the asset received under one event ID."""
    if policy.swap_valuation_rule is not SwapValuationRule.REQUIRE_AGREEING_LEGS:
        raise ValueError(
            f"Unsupported swap valuation rule: {policy.swap_valuation_rule.value}."
        )
    given, received = _swap_legs(event)
    given_price = resolve_price(
        prices,
        mint=given.mint,
        timestamp_utc=event.timestamp_utc,
        policy=policy,
    )
    received_price = resolve_price(
        prices,
        mint=received.mint,
        timestamp_utc=event.timestamp_utc,
        policy=policy,
    )
    price_source_ids = tuple(
        sorted({*given_price.source_ids, *received_price.source_ids})
    )

    exchange_value: Decimal | None = None
    exception_code: str | None = None
    reason = ""
    human_request = ""
    if (
        given_price.state is PriceResolutionState.CONFLICT
        or received_price.state is PriceResolutionState.CONFLICT
    ):
        exception_code = "conflicting_swap_valuation"
        reason = "At least one swap leg has multiple exact price sources."
        human_request = "Resolve the conflicting exact price evidence for both swap assets."
    elif (
        given_price.state is PriceResolutionState.MISSING
        and received_price.state is PriceResolutionState.MISSING
    ):
        exception_code = "missing_swap_valuation"
        reason = "Neither swap leg has an exact supported price."
        human_request = "Provide exact timestamp USD prices for both swap assets."
    elif (
        given_price.state is PriceResolutionState.MISSING
        or received_price.state is PriceResolutionState.MISSING
    ):
        exception_code = "one_sided_swap_valuation"
        reason = "Only one swap leg has an exact supported price."
        human_request = "Provide the missing exact timestamp USD price for the other asset."
    else:
        given_value = _implied_value(given, given_price)
        received_value = _implied_value(received, received_price)
        if given_value == received_value:
            exchange_value = given_value
        else:
            exception_code = "conflicting_swap_valuation"
            reason = "The two exact swap prices imply different exchange values."
            human_request = "Resolve which exact price evidence supports the exchange value."

    # Inventory leaves even when valuation is blocked; selection happens before receipt so
    # insufficient inventory cannot create only the acquisition side.
    consumptions = ledger.consume(event, leg=given)
    if exchange_value is not None:
        received_lot = ledger.acquire(
            event, leg=received, basis_usd=exchange_value
        )
        return Swap(
            valuation_state=EvidenceState.KNOWN,
            event_id=event.event_id,
            given_leg=given,
            received_leg=received,
            fee=event.fee,
            exchange_value_usd=exchange_value,
            price_source_ids=price_source_ids,
            consumptions=consumptions,
            received_lot=received_lot,
        )

    if exception_code is None:
        raise AssertionError("Unvalued swap has no exception classification.")
    exception = collector.add(
        code=exception_code,
        severity=ExceptionSeverity.MONETARY_OUTPUT_BLOCKING,
        stage=PipelineStage.ACCOUNTING,
        source_refs=event.source_refs,
        observed_facts=(
            ObservedFact(
                name="swap_valuation_rule", value=policy.swap_valuation_rule.value
            ),
            *_resolution_facts(side="given", leg=given, resolution=given_price),
            *_resolution_facts(
                side="received", leg=received, resolution=received_price
            ),
        ),
        reason=reason,
        affected_outputs=("disposals.csv",),
        related_object_ids=(
            event.event_id,
            given.component_id,
            received.component_id,
            *(item.consumption_id for item in consumptions),
            *price_source_ids,
        ),
        human_request=human_request,
    )
    received_lot = ledger.acquire_unknown(
        event,
        leg=received,
        exception_id=exception.exception_id,
    )
    return Swap(
        valuation_state=EvidenceState.UNKNOWN,
        event_id=event.event_id,
        given_leg=given,
        received_leg=received,
        fee=event.fee,
        exchange_value_usd=None,
        price_source_ids=price_source_ids,
        consumptions=consumptions,
        received_lot=received_lot,
        valuation_exception_id=exception.exception_id,
    )
