"""Coordinate fixture evidence, tax-lot accounting, and localized reports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from engine.accounting import (
    FifoLedger,
    InsufficientInventoryError,
    TaxLotLedger,
    account_fee,
    account_income,
    account_swap,
    account_wrap,
    reconcile_run,
)
from engine.core.exceptions import ExceptionCollector
from engine.country_US import UsTaxReportHandler
from engine.evidence.loader import load_fixture
from engine.evidence.processor import process_evidence
from engine.models import (
    Acquisition,
    Basis,
    Classification,
    ClassificationType,
    DisposalRow,
    DomainException,
    ExceptionSeverity,
    Fee,
    FeeContext,
    IncomeRow,
    LotConsumption,
    NormalizedEvent,
    ObservedFact,
    PipelineStage,
    PricePoint,
    ResolutionState,
    TokenLeg,
)
from engine.policy.config import (
    AssessmentPolicy,
    BasisMethod,
    require_supported_policy,
)
from engine.reporting.handler import TaxReportHandler
from engine.reporting.writer import write_reports

TraceSink = Callable[[str], None]

@dataclass(frozen=True, slots=True)
class RunResult:
    """Counts returned to the CLI after a successful pipeline run."""

    disposal_count: int
    income_count: int
    exception_count: int
    reconciliation_passed: bool

def _merge_exceptions(
    existing: tuple[DomainException, ...], collector: ExceptionCollector
) -> tuple[DomainException, ...]:
    by_id = {item.exception_id: item for item in (*existing, *collector.items())}
    return tuple(sorted(by_id.values(), key=lambda item: item.exception_id))

def _decimal_text(value: Decimal) -> str:
    return format(value, "f")

def _trace_decimal_text(value: Decimal) -> str:
    text = _decimal_text(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if "." not in text:
        return f"{text}.00"
    whole, fraction = text.split(".", maxsplit=1)
    return f"{whole}.{fraction.ljust(2, '0')}"

def _basis_text(basis: Basis) -> str:
    if basis.is_known:
        return f"USD {_trace_decimal_text(basis.require_total_usd())}"
    return f"unknown basis (see {basis.require_exception_id()})"

class WalletTaxEngine:
    """Run one configured evidence-to-report projection.

    The engine coordinates stages but delegates inventory behavior to `TaxLotLedger` and
    report interpretation to `TaxReportHandler`. Passing another ledger class or localization
    handler reuses loading, evidence processing, exception handling, and output writing.
    """

    def __init__(
        self,
        *,
        policy: AssessmentPolicy,
        ledger_class: type[TaxLotLedger] = FifoLedger,
        report_handler: TaxReportHandler | None = None,
    ) -> None:
        require_supported_policy(policy)
        if policy.basis_method is not BasisMethod.FIFO:
            raise ValueError(f"Unsupported core basis method: {policy.basis_method.value}.")
        self._ledger_class = ledger_class
        self._policy = policy
        self._report_handler = report_handler or UsTaxReportHandler(policy=policy)
        if self._report_handler.policy.key != policy.key:
            raise ValueError("Report handler policy differs from the engine policy.")

    @staticmethod
    def _trace(trace: TraceSink | None, message: str) -> None:
        if trace is not None:
            trace(message)

    def _prepare_disposal(
        self,
        *,
        event: NormalizedEvent,
        ledger: TaxLotLedger,
        collector: ExceptionCollector,
        trace: TraceSink | None,
        consumptions: tuple[LotConsumption, ...] | None = None,
        leg: TokenLeg | None = None,
        proceeds_usd: Decimal | None = None,
    ) -> tuple[DisposalRow, ...]:
        disposal_consumptions = (
            consumptions if consumptions is not None else ledger.consume(event, leg=leg)
        )
        proceeds = self._report_handler.allocate_disposal_proceeds(
            event=event,
            consumptions=disposal_consumptions,
            proceeds_usd=proceeds_usd,
        )
        rows = self._report_handler.prepare_disposal_rows(
            event=event,
            consumptions=disposal_consumptions,
            ledger=ledger,
            leg=leg,
            proceeds_usd=proceeds_usd,
        )
        self._trace(
            trace,
            f"    {ledger.__class__.__name__}.consume() -> "
            f"{len(disposal_consumptions)} slice(s); "
            f"{self._report_handler.__class__.__name__}.prepare_disposal_rows() -> "
            f"{len(rows)} complete row(s)",
        )
        for consumption, allocated in zip(disposal_consumptions, proceeds, strict=True):
            lot = ledger.get_lot(consumption.lot_id)
            self._trace(
                trace,
                f"      lot={lot.lot_id} quantity={consumption.quantity_base_units} "
                f"proceeds_usd={_trace_decimal_text(allocated)} "
                f"basis={_basis_text(consumption.consumed_basis)}",
            )
            if not consumption.consumed_basis.is_known:
                basis_exception_id = consumption.consumed_basis.require_exception_id()
                collector.add(
                    code="unknown_basis_disposal_slice",
                    severity=ExceptionSeverity.MONETARY_OUTPUT_BLOCKING,
                    stage=PipelineStage.ACCOUNTING,
                    source_refs=event.source_refs,
                    observed_facts=(
                        ObservedFact(
                            name="quantity_base_units",
                            value=str(consumption.quantity_base_units),
                        ),
                        ObservedFact(
                            name="allocated_proceeds_usd",
                            value=_decimal_text(allocated),
                        ),
                        ObservedFact(
                            name="originating_exception_id",
                            value=basis_exception_id,
                        ),
                    ),
                    reason="This disposal slice has proceeds but no defensible basis.",
                    affected_outputs=("disposals.csv",),
                    related_object_ids=(
                        event.event_id,
                        lot.lot_id,
                        basis_exception_id,
                    ),
                    human_request="Provide acquisition and cost-basis evidence for this lot.",
                )
            elif not lot.acquisition.is_known:
                collector.add(
                    code="unknown_acquisition_date_disposal_slice",
                    severity=ExceptionSeverity.MONETARY_OUTPUT_BLOCKING,
                    stage=PipelineStage.ACCOUNTING,
                    source_refs=event.source_refs,
                    observed_facts=(
                        ObservedFact(
                            name="quantity_base_units",
                            value=str(consumption.quantity_base_units),
                        ),
                        ObservedFact(
                            name="allocated_proceeds_usd",
                            value=_decimal_text(allocated),
                        ),
                    ),
                    reason="The original acquisition date cannot support a holding term.",
                    affected_outputs=("disposals.csv",),
                    related_object_ids=(event.event_id, lot.lot_id),
                    human_request="Provide evidence of the original acquisition timestamp.",
                )
        return rows

    def _prepare_swap(
        self,
        *,
        event: NormalizedEvent,
        prices: tuple[PricePoint, ...],
        ledger: TaxLotLedger,
        collector: ExceptionCollector,
        trace: TraceSink | None,
    ) -> tuple[DisposalRow, ...]:
        swap = account_swap(
            event=event,
            prices=prices,
            policy=self._policy,
            ledger=ledger,
            collector=collector,
        )
        fee_state = "charged" if swap.fee.charged else "not_charged"
        self._trace(
            trace,
            f"    account_swap() -> valuation={swap.valuation_state.value} "
            f"given={swap.given_leg.component_id} "
            f"received_lot={swap.received_lot.lot_id} fee={fee_state}",
        )
        fee_rows: tuple[DisposalRow, ...] = ()
        if event.fee.charged:
            _, fee_rows = self._prepare_fee(
                event=event,
                context=FeeContext.ACQUISITION,
                prices=prices,
                ledger=ledger,
                collector=collector,
                trace=trace,
                related_lot_id=swap.received_lot.lot_id,
            )
        if not swap.is_valued:
            self._trace(
                trace,
                f"      valuation blocked by {swap.valuation_exception_id}; "
                "received quantity preserved with unknown basis",
            )
            return fee_rows
        if swap.exchange_value_usd is None:
            raise AssertionError("Valued swap has no exchange value.")
        return (
            *self._prepare_disposal(
                event=event,
                ledger=ledger,
                collector=collector,
                trace=trace,
                consumptions=swap.consumptions,
                leg=swap.given_leg,
                proceeds_usd=swap.exchange_value_usd,
            ),
            *fee_rows,
        )

    def _prepare_fee(
        self,
        *,
        event: NormalizedEvent,
        context: FeeContext,
        prices: tuple[PricePoint, ...],
        ledger: TaxLotLedger,
        collector: ExceptionCollector,
        trace: TraceSink | None,
        related_lot_id: str | None = None,
        gross_proceeds_usd: Decimal | None = None,
    ) -> tuple[Fee, tuple[DisposalRow, ...]]:
        fee = account_fee(
            event=event,
            context=context,
            prices=prices,
            policy=self._policy,
            ledger=ledger,
            collector=collector,
            related_lot_id=related_lot_id,
            gross_proceeds_usd=gross_proceeds_usd,
        )
        self._trace(
            trace,
            f"    account_fee() -> context={context.value} "
            f"valuation={fee.valuation_state.value} event={fee.fee_event.event_id}",
        )
        if not fee.is_valued or fee.fee_value_usd is None:
            return fee, ()
        rows = self._prepare_disposal(
            event=fee.fee_event,
            ledger=ledger,
            collector=collector,
            trace=trace,
            consumptions=fee.consumptions,
            leg=fee.fee_event.token_legs[0],
            proceeds_usd=fee.fee_value_usd,
        )
        return fee, rows

    def _prepare_income(
        self,
        *,
        event: NormalizedEvent,
        classification_type: ClassificationType,
        prices: tuple[PricePoint, ...],
        ledger: TaxLotLedger,
        collector: ExceptionCollector,
        trace: TraceSink | None,
    ) -> tuple[IncomeRow, ...]:
        receipts = account_income(
            event=event,
            classification_type=classification_type,
            prices=prices,
            policy=self._policy,
            ledger=ledger,
            collector=collector,
        )
        rows = self._report_handler.prepare_income_rows(income=receipts)
        if rows:
            row = rows[0]
            self._trace(
                trace,
                f"    account_income() -> category={row.category} "
                f"income_usd={_trace_decimal_text(row.fair_value_usd)} "
                f"lot={row.created_lot_id}",
            )
        else:
            self._trace(
                trace,
                f"    account_income() -> category={classification_type.value} "
                "quantity preserved without fabricated income",
            )
        return rows

    def _prepare_external_inbound(
        self,
        *,
        event: NormalizedEvent,
        ledger: TaxLotLedger,
        collector: ExceptionCollector,
        trace: TraceSink | None,
    ) -> None:
        exception = collector.add(
            code="unknown_external_inbound_basis",
            severity=ExceptionSeverity.MONETARY_OUTPUT_BLOCKING,
            stage=PipelineStage.ACCOUNTING,
            source_refs=event.source_refs,
            observed_facts=(
                ObservedFact(
                    name="quantity_base_units",
                    value=str(event.token_legs[0].quantity_base_units),
                ),
            ),
            reason="Received quantity is known but its acquisition basis is not.",
            affected_outputs=("disposals.csv",),
            related_object_ids=(event.event_id,),
            human_request=(
                "Provide the original acquisition date, quantity, total basis, "
                "and supporting exchange or wallet records."
            ),
        )
        lot = ledger.acquire_unknown(
            event,
            exception_id=exception.exception_id,
            acquisition=Acquisition.unknown(
                observed_at_utc=event.timestamp_utc,
                exception_id=exception.exception_id,
            ),
        )
        self._trace(
            trace,
            f"    {ledger.__class__.__name__}.acquire_unknown() -> lot={lot.lot_id} "
            f"quantity={lot.starting_quantity_base_units} exception={exception.exception_id}",
        )

    def _prepare_external_outbound(
        self,
        *,
        event: NormalizedEvent,
        ledger: TaxLotLedger,
        collector: ExceptionCollector,
        trace: TraceSink | None,
    ) -> None:
        movements = ledger.suspend(event)
        collector.add(
            code="unknown_outbound_purpose",
            severity=ExceptionSeverity.MONETARY_OUTPUT_BLOCKING,
            stage=PipelineStage.ACCOUNTING,
            source_refs=event.source_refs,
            observed_facts=(
                ObservedFact(
                    name="quantity_base_units",
                    value=str(event.token_legs[0].quantity_base_units),
                ),
            ),
            reason="Outbound units left spendable inventory without a proven tax purpose.",
            affected_outputs=("disposals.csv",),
            related_object_ids=tuple(item.movement_id for item in movements),
            human_request="Confirm destination ownership and the purpose of this transfer.",
        )
        self._trace(
            trace,
            f"    {ledger.__class__.__name__}.suspend() -> {len(movements)} movement(s)",
        )

    def _process_event(
        self,
        *,
        event: NormalizedEvent,
        classification: Classification,
        prices: tuple[PricePoint, ...],
        ledger: TaxLotLedger,
        collector: ExceptionCollector,
        income_rows: list[IncomeRow],
        trace: TraceSink | None,
    ) -> tuple[DisposalRow, ...]:
        record_ids = ",".join(source.fixture_record_id for source in event.source_refs)
        self._trace(
            trace,
            f"  event={event.event_id} records={record_ids} "
            f"classification={classification.type.value} state={classification.state.value}",
        )
        if (
            classification.type is ClassificationType.SPAM_AIRDROP
            and classification.state is ResolutionState.REVIEW_FLAGGED
        ):
            spam_rows = self._prepare_income(
                event=event,
                classification_type=classification.type,
                prices=prices,
                ledger=ledger,
                collector=collector,
                trace=trace,
            )
            income_rows.extend(spam_rows)
            if event.fee.charged:
                _, fee_rows = self._prepare_fee(
                    event=event,
                    context=FeeContext.UNSUPPORTED,
                    prices=prices,
                    ledger=ledger,
                    collector=collector,
                    trace=trace,
                )
                return fee_rows
            return ()
        if classification.state is not ResolutionState.RESOLVED:
            self._trace(trace, "    accounting skipped because the event requires review")
            return ()
        try:
            if classification.type is ClassificationType.BUY:
                lot = ledger.acquire(event)
                self._trace(
                    trace,
                    f"    {ledger.__class__.__name__}.acquire() -> lot={lot.lot_id} "
                    f"quantity={lot.starting_quantity_base_units} "
                    f"basis={_basis_text(lot.starting_basis)}",
                )
                if event.fee.charged:
                    _, fee_rows = self._prepare_fee(
                        event=event,
                        context=FeeContext.ACQUISITION,
                        prices=prices,
                        ledger=ledger,
                        collector=collector,
                        trace=trace,
                        related_lot_id=lot.lot_id,
                    )
                    return fee_rows
            elif classification.type is ClassificationType.SELL:
                consumptions = ledger.consume(event)
                if not event.fee.charged:
                    return self._prepare_disposal(
                        event=event,
                        ledger=ledger,
                        collector=collector,
                        trace=trace,
                        consumptions=consumptions,
                    )
                fee, fee_rows = self._prepare_fee(
                    event=event,
                    context=FeeContext.DISPOSAL,
                    prices=prices,
                    ledger=ledger,
                    collector=collector,
                    trace=trace,
                    gross_proceeds_usd=event.cash_value_usd,
                )
                if (
                    not fee.is_valued
                    or not fee.treatment_is_supported
                    or fee.adjusted_proceeds_usd is None
                ):
                    return fee_rows
                return (
                    *self._prepare_disposal(
                        event=event,
                        ledger=ledger,
                        collector=collector,
                        trace=trace,
                        consumptions=consumptions,
                        proceeds_usd=fee.adjusted_proceeds_usd,
                    ),
                    *fee_rows,
                )
            elif classification.type is ClassificationType.SWAP:
                return self._prepare_swap(
                    event=event,
                    prices=prices,
                    ledger=ledger,
                    collector=collector,
                    trace=trace,
                )
            elif classification.type is ClassificationType.WRAP:
                wrap = account_wrap(event=event, ledger=ledger, collector=collector)
                self._trace(
                    trace,
                    f"    account_wrap() -> continuity={str(wrap.is_continuous).lower()} "
                    f"movements={len(wrap.movements)}",
                )
            elif classification.type in {
                ClassificationType.STAKING_REWARD,
                ClassificationType.AIRDROP,
            }:
                prepared_income = self._prepare_income(
                    event=event,
                    classification_type=classification.type,
                    prices=prices,
                    ledger=ledger,
                    collector=collector,
                    trace=trace,
                )
                income_rows.extend(prepared_income)
                if event.fee.charged:
                    related_lot_id = (
                        prepared_income[0].created_lot_id
                        if prepared_income
                        else next(
                            (
                                lot.lot_id
                                for lot in ledger.all_lots()
                                if lot.source_event_id == event.event_id
                            ),
                            None,
                        )
                    )
                    _, fee_rows = self._prepare_fee(
                        event=event,
                        context=FeeContext.UNSUPPORTED,
                        prices=prices,
                        ledger=ledger,
                        collector=collector,
                        trace=trace,
                        related_lot_id=related_lot_id,
                    )
                    return fee_rows
            elif classification.type is ClassificationType.INTERNAL_TRANSFER:
                if event.counterparty_wallet_id is None:
                    raise ValueError("Matched owned transfer has no destination wallet.")
                movements = ledger.move(event, destination_wallet_id=event.counterparty_wallet_id)
                self._trace(
                    trace,
                    f"    {ledger.__class__.__name__}.move() -> {len(movements)} "
                    f"carried lot slice(s) to {event.counterparty_wallet_id}",
                )
            elif classification.type is ClassificationType.EXTERNAL_INBOUND:
                self._prepare_external_inbound(
                    event=event, ledger=ledger, collector=collector, trace=trace
                )
            elif classification.type is ClassificationType.EXTERNAL_OUTBOUND:
                self._prepare_external_outbound(
                    event=event, ledger=ledger, collector=collector, trace=trace
                )
            elif classification.type in {
                ClassificationType.FAILED,
                ClassificationType.FEE,
            }:
                self._trace(trace, "    reverted economic legs create no tax-lot mutation")
            else:
                collector.add(
                    code="accounting_rule_not_yet_implemented",
                    severity=ExceptionSeverity.EVENT_BLOCKING,
                    stage=PipelineStage.ACCOUNTING,
                    source_refs=event.source_refs,
                    observed_facts=(ObservedFact(name="type", value=classification.type.value),),
                    reason="Classification is known but its accounting rule is outside product.",
                    affected_outputs=("disposals.csv", "income.csv"),
                    related_object_ids=(event.event_id,),
                    human_request=("Review this event after its accounting policy is implemented."),
                )
        except InsufficientInventoryError as error:
            collector.add(
                code="insufficient_wallet_inventory",
                severity=ExceptionSeverity.EVENT_BLOCKING,
                stage=PipelineStage.ACCOUNTING,
                source_refs=event.source_refs,
                observed_facts=(ObservedFact(name="inventory_error", value=str(error)),),
                reason="The operation would make wallet-and-mint inventory negative.",
                affected_outputs=("disposals.csv", "income.csv"),
                related_object_ids=(event.event_id,),
                human_request="Provide prior acquisition or inbound-transfer evidence.",
            )
            self._trace(trace, f"    accounting blocked: {error}")
        if event.fee.charged:
            context = (
                FeeContext.FAILED
                if classification.type is ClassificationType.FAILED
                else FeeContext.STANDALONE
            )
            _, fee_rows = self._prepare_fee(
                event=event,
                context=context,
                prices=prices,
                ledger=ledger,
                collector=collector,
                trace=trace,
            )
            return fee_rows
        return ()

    def run(
        self, *, fixtures_dir: Path, output_dir: Path, trace: TraceSink | None = None
    ) -> RunResult:
        """Execute the configured stages and optionally emit a beginner-readable trace."""
        self._trace(trace, "[1/4] Loading and validating fixture evidence")
        fixture = load_fixture(fixtures_dir, policy=self._policy)
        self._trace(
            trace,
            f"  load_fixture() -> {len(fixture.all_source_refs)} stored record(s), "
            f"{len(fixture.transactions)} valid transaction(s), "
            f"{len(fixture.load_exceptions)} quarantined record(s)",
        )

        self._trace(trace, "[2/4] Normalizing, matching, and classifying evidence")
        evidence = process_evidence(fixture)
        coverage_counts: dict[str, int] = {}
        for entry in evidence.coverage:
            coverage_counts[entry.status.value] = coverage_counts.get(entry.status.value, 0) + 1
        self._trace(
            trace,
            f"  process_evidence() -> {len(evidence.events)} logical event(s), "
            f"coverage={coverage_counts}",
        )

        ledger = self._ledger_class()
        collector = ExceptionCollector()
        disposals: list[DisposalRow] = []
        income: list[IncomeRow] = []
        self._trace(
            trace,
            f"[3/4] Accounting with {ledger.__class__.__name__} "
            f"(method={ledger.method}) and {self._report_handler.__class__.__name__} "
            f"(localization={self._report_handler.localization})",
        )
        for event, classification in zip(evidence.events, evidence.classifications, strict=True):
            disposals.extend(
                self._process_event(
                    event=event,
                    classification=classification,
                    prices=fixture.prices,
                    ledger=ledger,
                    collector=collector,
                    income_rows=income,
                    trace=trace,
                )
            )

        exceptions = _merge_exceptions(evidence.exceptions, collector)
        reconciliation = reconcile_run(
            ledger=ledger,
            disposals=tuple(disposals),
            income=tuple(income),
        )
        self._trace(
            trace,
            "  reconcile_run() -> "
            f"lots={reconciliation.lot_count}, "
            f"consumptions={reconciliation.consumption_count}, "
            f"movements={reconciliation.movement_count}, passed=true",
        )
        self._trace(trace, "[4/4] Writing deterministic reports")
        write_reports(
            output_dir=output_dir,
            fixture_set_id=fixture.fixture_set_id,
            policy=self._policy,
            disposals=tuple(disposals),
            income=tuple(income),
            exceptions=exceptions,
        )
        self._trace(
            trace,
            f"  write_reports() -> disposals={len(disposals)}, income={len(income)}, "
            f"exceptions={len(exceptions)}, output={output_dir}",
        )
        return RunResult(
            disposal_count=len(disposals),
            income_count=len(income),
            exception_count=len(exceptions),
            reconciliation_passed=True,
        )

def run_pipeline(
    *,
    fixtures_dir: Path,
    output_dir: Path,
    policy: AssessmentPolicy,
    trace: TraceSink | None = None,
) -> RunResult:
    """Run one explicitly selected FIFO and U.S. assessment policy."""
    return WalletTaxEngine(policy=policy).run(
        fixtures_dir=fixtures_dir, output_dir=output_dir, trace=trace
    )
