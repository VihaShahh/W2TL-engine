"""Account for proven token-paid fees under the selected event-context policy."""

from __future__ import annotations

from decimal import Decimal

from engine.accounting.lots import (
    InsufficientInventoryError,
    TaxLotLedger,
)
from engine.core.exceptions import ExceptionCollector
from engine.core.identity import stable_id
from engine.core.values import base_units_to_decimal
from engine.models import (
    Direction,
    EvidenceState,
    ExceptionSeverity,
    Fee,
    FeeContext,
    FeeEvidence,
    LotConsumption,
    NormalizedEvent,
    ObservedFact,
    PipelineStage,
    PricePoint,
    PriceResolutionState,
    TokenLeg,
    TransactionStatus,
)
from engine.policy.config import AssessmentPolicy, FeeRule
from engine.policy.pricing import resolve_price


def _prepare_fee_event(event: NormalizedEvent) -> NormalizedEvent:
    evidence = event.fee
    mint = evidence.require_mint()
    fee_event_id = stable_id("evt_fee", event.event_id, evidence.payer_wallet_id, mint)
    return NormalizedEvent(
        event_id=fee_event_id,
        source_refs=event.source_refs,
        timestamp_utc=event.timestamp_utc,
        slot=event.slot,
        instruction_index=event.instruction_index,
        wallet_id=evidence.payer_wallet_id,
        status=TransactionStatus.SUCCEEDED,
        program=event.program,
        instruction_type="fee",
        token_legs=(
            TokenLeg(
                component_id=stable_id("cmp_fee", fee_event_id, mint),
                mint=mint,
                symbol=evidence.require_symbol(),
                decimals=evidence.require_decimals(),
                direction=Direction.OUTBOUND,
                quantity_base_units=evidence.fee_base_units,
            ),
        ),
        fee=FeeEvidence(
            payer_wallet_id=evidence.payer_wallet_id,
            fee_base_units=0,
            charged=False,
        ),
        cash_value_usd=None,
        counterparty_wallet_id=None,
    )

def _add_fee_exception(
    *,
    collector: ExceptionCollector,
    event: NormalizedEvent,
    fee_event: NormalizedEvent,
    code: str,
    observed_name: str,
    observed_value: str,
    reason: str,
    human_request: str,
    related_lot_id: str | None,
) -> str:
    related_ids = [event.event_id, fee_event.event_id]
    if related_lot_id is not None:
        related_ids.append(related_lot_id)
    exception = collector.add(
        code=code,
        severity=ExceptionSeverity.MONETARY_OUTPUT_BLOCKING,
        stage=PipelineStage.ACCOUNTING,
        source_refs=event.source_refs,
        observed_facts=(
            ObservedFact(name="fee_mint", value=event.fee.require_mint()),
            ObservedFact(name="fee_quantity_base_units", value=str(event.fee.fee_base_units)),
            ObservedFact(name=observed_name, value=observed_value),
        ),
        reason=reason,
        affected_outputs=("disposals.csv", "income.csv"),
        related_object_ids=tuple(related_ids),
        human_request=human_request,
    )
    return exception.exception_id

def _mark_related_basis_unknown(
    *, ledger: TaxLotLedger, related_lot_id: str | None, exception_id: str
) -> None:
    if related_lot_id is None:
        return
    lot = ledger.get_lot(related_lot_id)
    if lot.starting_basis.is_known:
        ledger.mark_basis_unknown(related_lot_id, exception_id=exception_id)

def _unvalued_fee(
    *,
    context: FeeContext,
    event: NormalizedEvent,
    fee_event: NormalizedEvent,
    consumptions: tuple[LotConsumption, ...],
    related_lot_id: str | None,
    exception_id: str,
) -> Fee:
    return Fee(
        valuation_state=EvidenceState.UNKNOWN,
        context=context,
        parent_event_id=event.event_id,
        fee_event=fee_event,
        fee_value_usd=None,
        price_source_id=None,
        consumptions=consumptions,
        related_lot_id=related_lot_id,
        valuation_exception_id=exception_id,
    )

def account_fee(
    *,
    event: NormalizedEvent,
    context: FeeContext,
    prices: tuple[PricePoint, ...],
    policy: AssessmentPolicy,
    ledger: TaxLotLedger,
    collector: ExceptionCollector,
    related_lot_id: str | None = None,
    gross_proceeds_usd: Decimal | None = None,
) -> Fee:
    """Consume and value one fee, then apply its supported related adjustment once."""
    if policy.fee_rule is not FeeRule.EVENT_CONTEXT:
        raise ValueError(f"Unsupported fee rule: {policy.fee_rule.value}.")
    if not event.fee.charged:
        raise ValueError(f"Event {event.event_id} has no charged fee.")

    fee_event = _prepare_fee_event(event)
    fee_mint = event.fee.require_mint()
    economic_mints = {leg.mint for leg in event.token_legs}
    if context in {FeeContext.ACQUISITION, FeeContext.DISPOSAL} and fee_mint in economic_mints:
        exception_id = _add_fee_exception(
            collector=collector,
            event=event,
            fee_event=fee_event,
            code="unsupported_fee_token_overlap",
            observed_name="event_context",
            observed_value=context.value,
            reason=(
                "The fee token overlaps an economic leg, so gross and net quantities cannot "
                "be distinguished under the current fixture contract."
            ),
            human_request="Provide separate gross economic legs and fee-token balance evidence.",
            related_lot_id=related_lot_id,
        )
        _mark_related_basis_unknown(
            ledger=ledger, related_lot_id=related_lot_id, exception_id=exception_id
        )
        return _unvalued_fee(
            context=context,
            event=event,
            fee_event=fee_event,
            consumptions=(),
            related_lot_id=related_lot_id,
            exception_id=exception_id,
        )

    try:
        consumptions = ledger.consume(fee_event)
    except InsufficientInventoryError as error:
        exception_id = _add_fee_exception(
            collector=collector,
            event=event,
            fee_event=fee_event,
            code="insufficient_fee_inventory",
            observed_name="inventory_error",
            observed_value=str(error),
            reason=(
                "The proven fee cannot consume more represented fee-token inventory than exists."
            ),
            human_request="Provide prior fee-token acquisition or inbound-transfer evidence.",
            related_lot_id=related_lot_id,
        )
        _mark_related_basis_unknown(
            ledger=ledger, related_lot_id=related_lot_id, exception_id=exception_id
        )
        return _unvalued_fee(
            context=context,
            event=event,
            fee_event=fee_event,
            consumptions=(),
            related_lot_id=related_lot_id,
            exception_id=exception_id,
        )

    resolution = resolve_price(
        prices,
        mint=fee_mint,
        timestamp_utc=event.timestamp_utc,
        policy=policy,
    )
    if resolution.state is not PriceResolutionState.FOUND:
        code = (
            "missing_fee_price"
            if resolution.state is PriceResolutionState.MISSING
            else "conflicting_fee_price"
        )
        exception_id = _add_fee_exception(
            collector=collector,
            event=event,
            fee_event=fee_event,
            code=code,
            observed_name="price_source_ids",
            observed_value=",".join(resolution.source_ids) or "none",
            reason="The proven fee quantity has no single exact supported USD value.",
            human_request="Provide one exact timestamp USD price source for the fee token.",
            related_lot_id=related_lot_id,
        )
        _mark_related_basis_unknown(
            ledger=ledger, related_lot_id=related_lot_id, exception_id=exception_id
        )
        return _unvalued_fee(
            context=context,
            event=event,
            fee_event=fee_event,
            consumptions=consumptions,
            related_lot_id=related_lot_id,
            exception_id=exception_id,
        )

    fee_value_usd = (
        base_units_to_decimal(event.fee.fee_base_units, decimals=event.fee.require_decimals())
        * resolution.require_price_usd()
    )
    adjusted_proceeds_usd: Decimal | None = None
    treatment_exception_id: str | None = None

    if context is FeeContext.ACQUISITION:
        if related_lot_id is None:
            raise ValueError("Acquisition fee requires its acquired lot ID.")
        related_lot = ledger.get_lot(related_lot_id)
        if related_lot.starting_basis.is_known:
            ledger.adjust_basis(related_lot_id, adjustment_usd=fee_value_usd)
        else:
            treatment_exception_id = _add_fee_exception(
                collector=collector,
                event=event,
                fee_event=fee_event,
                code="fee_adjustment_blocked_by_unknown_basis",
                observed_name="related_lot_id",
                observed_value=related_lot_id,
                reason="The fee is valued, but its acquired lot already has unresolved basis.",
                human_request="Resolve the acquired lot basis before applying this fee adjustment.",
                related_lot_id=related_lot_id,
            )
    elif context is FeeContext.DISPOSAL:
        if gross_proceeds_usd is None or gross_proceeds_usd < fee_value_usd:
            treatment_exception_id = _add_fee_exception(
                collector=collector,
                event=event,
                fee_event=fee_event,
                code="unsupported_disposal_fee_adjustment",
                observed_name="gross_proceeds_usd",
                observed_value=(
                    format(gross_proceeds_usd, "f") if gross_proceeds_usd is not None else "missing"
                ),
                reason="The disposal fee cannot be applied to supported non-negative proceeds.",
                human_request=(
                    "Provide supported gross proceeds greater than or equal to the fee value."
                ),
                related_lot_id=related_lot_id,
            )
        else:
            adjusted_proceeds_usd = gross_proceeds_usd - fee_value_usd
    elif context is FeeContext.UNSUPPORTED:
        treatment_exception_id = _add_fee_exception(
            collector=collector,
            event=event,
            fee_event=fee_event,
            code="unsupported_fee_context",
            observed_name="instruction_type",
            observed_value=event.instruction_type,
            reason="The fee disposal is supported, but no related event adjustment is defined.",
            human_request="Review and approve a fee treatment for this event context.",
            related_lot_id=related_lot_id,
        )
        _mark_related_basis_unknown(
            ledger=ledger,
            related_lot_id=related_lot_id,
            exception_id=treatment_exception_id,
        )

    return Fee(
        valuation_state=EvidenceState.KNOWN,
        context=context,
        parent_event_id=event.event_id,
        fee_event=fee_event,
        fee_value_usd=fee_value_usd,
        price_source_id=resolution.require_source_id(),
        consumptions=consumptions,
        related_lot_id=related_lot_id,
        adjusted_proceeds_usd=adjusted_proceeds_usd,
        treatment_exception_id=treatment_exception_id,
    )
