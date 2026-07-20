"""Prepare U.S. 8949-shaped disposal rows from complete lot consumptions."""

from __future__ import annotations

from decimal import Decimal

from engine.accounting import TaxLotLedger, holding_term
from engine.core.identity import stable_id
from engine.core.values import base_units_to_decimal
from engine.models import (
    DisposalRow,
    Income,
    IncomeRow,
    LotConsumption,
    NormalizedEvent,
    TokenLeg,
)
from engine.reporting.handler import TaxReportHandler


class UsTaxReportHandler(TaxReportHandler):
    """Map defensible consumed slices to the current U.S. assessment output."""

    @property
    def localization(self) -> str:
        return "country_US"

    def prepare_disposal_rows(
        self,
        *,
        event: NormalizedEvent,
        consumptions: tuple[LotConsumption, ...],
        ledger: TaxLotLedger,
        leg: TokenLeg | None = None,
        proceeds_usd: Decimal | None = None,
    ) -> tuple[DisposalRow, ...]:
        """Build one 8949-shaped row for each slice with basis, date, and proceeds."""
        disposal_leg = leg or event.token_legs[0]
        rows: list[DisposalRow] = []
        proceeds_by_slice = self.allocate_disposal_proceeds(
            event=event,
            consumptions=consumptions,
            proceeds_usd=proceeds_usd,
        )
        for consumption, proceeds in zip(
            consumptions, proceeds_by_slice, strict=True
        ):
            lot = ledger.get_lot(consumption.lot_id)
            if not consumption.consumed_basis.is_known or not lot.acquisition.is_known:
                continue
            term = holding_term(
                lot.acquisition,
                event.timestamp_utc,
                rule=self.policy.holding_term_rule,
            )
            if term is None:
                continue
            cost_basis = consumption.consumed_basis.require_total_usd()
            rows.append(
                DisposalRow(
                    disposal_id=stable_id(
                        "disp", event.event_id, consumption.consumption_id
                    ),
                    event_id=event.event_id,
                    source_id=event.source_refs[0].source_id,
                    lot_id=lot.lot_id,
                    consumption_id=consumption.consumption_id,
                    wallet_id=event.wallet_id,
                    mint=disposal_leg.mint,
                    symbol=disposal_leg.symbol,
                    quantity_base_units=consumption.quantity_base_units,
                    quantity=base_units_to_decimal(
                        consumption.quantity_base_units, decimals=disposal_leg.decimals
                    ),
                    date_acquired=lot.acquisition.require_timestamp_utc(),
                    date_disposed=event.timestamp_utc,
                    proceeds_usd=proceeds,
                    cost_basis_usd=cost_basis,
                    gain_loss_usd=proceeds - cost_basis,
                    term=term,
                )
            )
        return tuple(rows)

    def prepare_income_rows(self, *, income: tuple[Income, ...]) -> tuple[IncomeRow, ...]:
        """Build one source-linked U.S. ordinary-income row per valued receipt."""
        return tuple(
            IncomeRow(
                income_id=stable_id(
                    "inc", item.event_id, item.created_lot_id, item.price_source_id
                ),
                event_id=item.event_id,
                source_id=item.source_id,
                wallet_id=item.wallet_id,
                mint=item.leg.mint,
                symbol=item.leg.symbol,
                category=item.category.value,
                quantity_base_units=item.leg.quantity_base_units,
                quantity=item.quantity,
                received_at=item.received_at,
                price_usd=item.price_usd,
                price_source_id=item.price_source_id,
                fair_value_usd=item.fair_value_usd,
                created_lot_id=item.created_lot_id,
            )
            for item in income
        )
