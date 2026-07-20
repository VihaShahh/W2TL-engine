"""Define the jurisdiction boundary over neutral tax-lot accounting results."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal

from engine.accounting import TaxLotLedger
from engine.models import (
    DisposalRow,
    Income,
    IncomeRow,
    LotConsumption,
    NormalizedEvent,
    TokenLeg,
)
from engine.policy.config import AssessmentPolicy, require_supported_policy


class TaxReportHandler(ABC):
    """Prepare jurisdiction-specific rows without changing accounting state.

    The ledger provides exact consumptions and lineage. A localization handler interprets
    those results for one report contract, while shared writers remain responsible for stable
    CSV and JSON serialization.
    """

    def __init__(self, *, policy: AssessmentPolicy) -> None:
        require_supported_policy(policy)
        self._policy = policy

    @property
    def policy(self) -> AssessmentPolicy:
        return self._policy

    @property
    @abstractmethod
    def localization(self) -> str:
        """Return the stable `country_<country>` identifier used by traces."""

    @staticmethod
    def allocate_disposal_proceeds(
        *,
        event: NormalizedEvent,
        consumptions: tuple[LotConsumption, ...],
        proceeds_usd: Decimal | None = None,
    ) -> tuple[Decimal, ...]:
        """Allocate exact proceeds by quantity and give the final slice the remainder."""
        total_proceeds = proceeds_usd if proceeds_usd is not None else event.cash_value_usd
        if total_proceeds is None:
            raise ValueError(f"Disposal event {event.event_id} has no known proceeds.")
        total_quantity = sum(item.quantity_base_units for item in consumptions)
        if total_quantity <= 0:
            return ()
        remaining = total_proceeds
        allocated: list[Decimal] = []
        for index, consumption in enumerate(consumptions):
            if index == len(consumptions) - 1:
                proceeds = remaining
            else:
                proceeds = total_proceeds * (
                    Decimal(consumption.quantity_base_units) / Decimal(total_quantity)
                )
                remaining -= proceeds
            allocated.append(proceeds)
        return tuple(allocated)

    @abstractmethod
    def prepare_disposal_rows(
        self,
        *,
        event: NormalizedEvent,
        consumptions: tuple[LotConsumption, ...],
        ledger: TaxLotLedger,
        leg: TokenLeg | None = None,
        proceeds_usd: Decimal | None = None,
    ) -> tuple[DisposalRow, ...]:
        """Prepare complete report rows and omit slices blocked by unknown facts."""

    @abstractmethod
    def prepare_income_rows(self, *, income: tuple[Income, ...]) -> tuple[IncomeRow, ...]:
        """Prepare jurisdiction-specific ordinary-income rows from valued receipts."""
