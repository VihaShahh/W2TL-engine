"""Immutable disposal, income, and independent ground-truth contracts.

These values sit at the report boundary and retain upstream identity. Writers may format them,
but presentation cannot mutate the accounting facts to make an incomplete row look complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from engine.models.accounting import Lot, LotConsumption
from engine.models.events import Classification
from engine.models.evidence import GroundTruthMetadata
from engine.models.states import HoldingTerm


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """Reviewed expectations for classifications, inventory, reports, and exceptions.

    Tests compare multiple intermediate layers because a correct total can hide the wrong lot,
    wallet, acquisition date, or lineage.
    """

    metadata: GroundTruthMetadata
    classifications: tuple[Classification, ...]
    expected_lots: tuple[Lot, ...]
    expected_consumptions: tuple[LotConsumption, ...]
    expected_disposals: tuple[DisposalRow, ...]
    expected_income: tuple[IncomeRow, ...]
    expected_exception_codes: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class DisposalRow:
    """Complete 8949-shaped row for one defensible consumed lot slice.

    One parent sale may yield several rows when it consumes lots with different dates or basis.
    Every row keeps source, event, lot, and consumption identity for review.
    """

    disposal_id: str
    event_id: str
    source_id: str
    lot_id: str
    consumption_id: str
    wallet_id: str
    mint: str
    symbol: str
    quantity_base_units: int
    quantity: Decimal
    date_acquired: datetime
    date_disposed: datetime
    proceeds_usd: Decimal
    cost_basis_usd: Decimal
    gain_loss_usd: Decimal
    term: HoldingTerm

@dataclass(frozen=True, slots=True)
class IncomeRow:
    """Jurisdiction-shaped valued receipt with price and created-lot lineage.

    The row remains separate from neutral ``Income`` so another country adapter can map the same
    accounting facts without inheriting this output shape.
    """

    income_id: str
    event_id: str
    source_id: str
    wallet_id: str
    mint: str
    symbol: str
    category: str
    quantity_base_units: int
    quantity: Decimal
    received_at: datetime
    price_usd: Decimal
    price_source_id: str
    fair_value_usd: Decimal
    created_lot_id: str
