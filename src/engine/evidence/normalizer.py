"""Normalize raw chain evidence without assigning tax treatment."""

from __future__ import annotations

from dataclasses import dataclass

from engine.core.exceptions import ExceptionCollector
from engine.core.identity import derive_event_id, stable_id
from engine.models import (
    Direction,
    DomainException,
    ExceptionSeverity,
    FixtureBundle,
    NormalizedEvent,
    ObservedFact,
    PipelineStage,
    TokenLeg,
    Transaction,
)


class NormalizationError(ValueError):
    """Raised when one record cannot form a defensible canonical event."""

@dataclass(frozen=True, slots=True)
class NormalizationResult:
    events: tuple[NormalizedEvent, ...]
    quarantined_source_ids: frozenset[str]
    exceptions: tuple[DomainException, ...]

def event_order_key(event: NormalizedEvent) -> tuple[object, ...]:
    """Use source facts only, so fixture file order can never affect FIFO."""
    primary = min(event.source_refs, key=lambda source: source.source_id)
    return (
        event.timestamp_utc,
        event.slot,
        primary.signature,
        event.instruction_index,
        event.wallet_id,
        primary.fixture_record_id,
    )

def _shared_value(values: set[str], *, field: str) -> str:
    if len(values) != 1:
        raise NormalizationError(f"Record has conflicting {field}: {sorted(values)}.")
    return next(iter(values))

def _optional_shared_value(values: set[str]) -> str | None:
    if len(values) > 1:
        raise NormalizationError(f"Record has conflicting counterparties: {sorted(values)}.")
    return next(iter(values)) if values else None

def _normalize_transaction(
    transaction: Transaction,
    *,
    token_symbols: dict[str, tuple[str, int]],
) -> NormalizedEvent:
    source = transaction.source
    event_id = derive_event_id(source_id=source.source_id)
    instructions = transaction.instructions
    changes = tuple(
        change for change in transaction.token_balance_changes if change.delta_base_units
    )

    if not instructions:
        if not transaction.fee.charged:
            raise NormalizationError("Record has neither instruction evidence nor a charged fee.")
        program = "fee_only"
        instruction_type = "fee"
        cash_value = None
        counterparty = None
    else:
        program = _shared_value(
            {instruction.program for instruction in instructions}, field="program evidence"
        )
        instruction_type = _shared_value(
            {instruction.instruction_type for instruction in instructions},
            field="instruction types",
        )
        cash_values = {
            instruction.cash_value_usd
            for instruction in instructions
            if instruction.cash_value_usd is not None
        }
        if len(cash_values) > 1:
            raise NormalizationError("Record has conflicting cash values across instructions.")
        cash_value = next(iter(cash_values)) if cash_values else None
        counterparty = _optional_shared_value(
            {
                instruction.counterparty_wallet_id
                for instruction in instructions
                if instruction.counterparty_wallet_id is not None
            }
        )

    legs: list[TokenLeg] = []
    for index, change in enumerate(changes):
        metadata = token_symbols.get(change.mint)
        if metadata is None:
            raise NormalizationError(f"No token metadata exists for mint {change.mint}.")
        matching_instructions = tuple(
            instruction
            for instruction in instructions
            if instruction.mint == change.mint
            and instruction.quantity_base_units == abs(change.delta_base_units)
        )
        if not matching_instructions:
            raise NormalizationError(
                f"Instruction evidence does not reconcile with the {change.mint} balance delta."
            )
        direction = Direction.INBOUND if change.delta_base_units > 0 else Direction.OUTBOUND
        symbol, decimals = metadata
        legs.append(
            TokenLeg(
                component_id=stable_id(
                    "cmp", event_id, change.mint, direction.value, index
                ),
                mint=change.mint,
                symbol=symbol,
                decimals=decimals,
                direction=direction,
                quantity_base_units=abs(change.delta_base_units),
            )
        )

    if not legs and not transaction.fee.charged and transaction.status.value != "failed":
        raise NormalizationError("A successful non-fee record has no economic balance change.")
    return NormalizedEvent(
        event_id=event_id,
        source_refs=(source,),
        timestamp_utc=transaction.timestamp_utc,
        slot=transaction.slot,
        instruction_index=transaction.instruction_index,
        wallet_id=source.wallet_id,
        status=transaction.status,
        program=program,
        instruction_type=instruction_type,
        token_legs=tuple(legs),
        fee=transaction.fee,
        cash_value_usd=cash_value,
        counterparty_wallet_id=counterparty,
    )

def normalize_fixture(bundle: FixtureBundle) -> NormalizationResult:
    """Normalize each valid record and quarantine only the record that cannot reconcile."""
    token_symbols = {token.mint: (token.symbol, token.decimals) for token in bundle.tokens}
    collector = ExceptionCollector()
    events: list[NormalizedEvent] = []
    quarantined: set[str] = set()
    for transaction in bundle.transactions:
        try:
            events.append(_normalize_transaction(transaction, token_symbols=token_symbols))
        except NormalizationError as error:
            quarantined.add(transaction.source.source_id)
            collector.add(
                code="unreconciled_source_record",
                severity=ExceptionSeverity.EVENT_BLOCKING,
                stage=PipelineStage.NORMALIZATION,
                source_refs=(transaction.source,),
                observed_facts=(ObservedFact(name="normalization_error", value=str(error)),),
                reason="Instruction and balance evidence cannot form one neutral event.",
                affected_outputs=("disposals.csv", "income.csv", "exceptions.json"),
                related_object_ids=(),
                human_request="Provide reconciled instruction, token, and balance evidence.",
            )
    return NormalizationResult(
        events=tuple(sorted(events, key=event_order_key)),
        quarantined_source_ids=frozenset(quarantined),
        exceptions=collector.items(),
    )
