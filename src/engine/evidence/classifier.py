"""Classify normalized evidence using one explicit precedence table."""

from __future__ import annotations

from engine.models import (
    Classification,
    ClassificationType,
    Direction,
    NormalizedEvent,
    ResolutionState,
    TransactionStatus,
)


def classify_event(event: NormalizedEvent) -> Classification:
    """Resolve the first matching rule so broad transfer rules cannot win early."""
    directions = {leg.direction for leg in event.token_legs}
    has_positive_cash = event.cash_value_usd is not None and event.cash_value_usd > 0

    if event.status is TransactionStatus.FAILED:
        classification_type, state, rule_id = (
            ClassificationType.FAILED,
            ResolutionState.RESOLVED,
            "core.failed",
        )
    elif event.instruction_type == "internal_transfer":
        classification_type, state, rule_id = (
            ClassificationType.INTERNAL_TRANSFER,
            ResolutionState.RESOLVED,
            "core.owned_transfer",
        )
    elif event.instruction_type == "staking_reward" and directions == {Direction.INBOUND}:
        classification_type, state, rule_id = (
            ClassificationType.STAKING_REWARD,
            ResolutionState.RESOLVED,
            "core.staking_reward",
        )
    elif event.instruction_type == "spam_airdrop" and directions == {Direction.INBOUND}:
        classification_type, state, rule_id = (
            ClassificationType.SPAM_AIRDROP,
            ResolutionState.REVIEW_FLAGGED,
            "core.spam_airdrop",
        )
    elif event.instruction_type == "airdrop" and directions == {Direction.INBOUND}:
        classification_type, state, rule_id = (
            ClassificationType.AIRDROP,
            ResolutionState.RESOLVED,
            "core.airdrop",
        )
    elif event.instruction_type == "wrap" and directions == {
        Direction.INBOUND,
        Direction.OUTBOUND,
    }:
        classification_type, state, rule_id = (
            ClassificationType.WRAP,
            ResolutionState.RESOLVED,
            "core.wrap",
        )
    elif event.instruction_type == "swap" and directions == {
        Direction.INBOUND,
        Direction.OUTBOUND,
    }:
        classification_type, state, rule_id = (
            ClassificationType.SWAP,
            ResolutionState.RESOLVED,
            "core.swap",
        )
    elif (
        event.program == "synthetic_exchange"
        and event.instruction_type == "buy"
        and directions == {Direction.INBOUND}
        and has_positive_cash
    ):
        classification_type, state, rule_id = (
            ClassificationType.BUY,
            ResolutionState.RESOLVED,
            "core.exchange.buy",
        )
    elif (
        event.program == "synthetic_exchange"
        and event.instruction_type == "sell"
        and directions == {Direction.OUTBOUND}
        and has_positive_cash
    ):
        classification_type, state, rule_id = (
            ClassificationType.SELL,
            ResolutionState.RESOLVED,
            "core.exchange.sell",
        )
    elif event.instruction_type == "transfer" and directions == {Direction.INBOUND}:
        classification_type, state, rule_id = (
            ClassificationType.EXTERNAL_INBOUND,
            ResolutionState.RESOLVED,
            "core.external_inbound",
        )
    elif event.instruction_type == "transfer" and directions == {Direction.OUTBOUND}:
        classification_type, state, rule_id = (
            ClassificationType.EXTERNAL_OUTBOUND,
            ResolutionState.RESOLVED,
            "core.external_outbound",
        )
    elif not event.token_legs and event.fee.charged:
        classification_type, state, rule_id = (
            ClassificationType.FEE,
            ResolutionState.RESOLVED,
            "core.fee_only",
        )
    else:
        classification_type, state, rule_id = (
            ClassificationType.UNRECOGNIZED,
            ResolutionState.UNRESOLVED,
            "core.unrecognized",
        )
    return Classification(
        event_id=event.event_id,
        type=classification_type,
        state=state,
        rule_id=rule_id,
        evidence_source_ids=tuple(source.source_id for source in event.source_refs),
    )
