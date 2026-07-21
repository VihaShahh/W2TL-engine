"""Build the versioned 96-record synthetic finalized fixture set."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "fixtures" / "complete"
FIXTURE_SET_ID = "final_wallet_history"
ALPHA = "synthetic_wallet_alpha"
BETA = "synthetic_wallet_beta"
SKITO = "synthetic_mint_skito"
NOVA = "synthetic_mint_nova"
SOL = "synthetic_mint_sol"
RWD = "synthetic_mint_reward"
DROP = "synthetic_mint_drop"
SPAM = "synthetic_mint_spam"
MYST = "synthetic_mint_mystery"
WSKITO = "synthetic_mint_wrapped_skito"

TOKEN_DECIMALS = {
    SKITO: 6,
    NOVA: 6,
    SOL: 9,
    RWD: 6,
    DROP: 6,
    SPAM: 0,
    MYST: 6,
    WSKITO: 6,
}

def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

def _instruction(
    instruction_type: str,
    mint: str,
    quantity: int,
    *,
    program: str,
    cash: str | None = None,
    counterparty: str | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "program": program,
        "instruction_type": instruction_type,
        "mint": mint,
        "quantity_base_units": quantity,
    }
    if cash is not None:
        result["cash_value_usd"] = cash
    if counterparty is not None:
        result["counterparty_wallet_id"] = counterparty
    return result

def _balance(mint: str, delta: int) -> dict[str, object]:
    pre = abs(delta) if delta < 0 else 0
    post = 0 if delta < 0 else delta
    return {
        "mint": mint,
        "pre_balance_base_units": pre,
        "post_balance_base_units": post,
        "delta_base_units": delta,
    }

def main() -> None:
    records: dict[str, list[dict[str, object]]] = {ALPHA: [], BETA: []}
    scenarios: dict[str, list[str]] = {
        "foundational-buys": [],
        "fifo-sales-and-holding": [],
        "owned-wallet-transfers": [],
        "external-and-unknown-basis": [],
        "swaps-and-wraps": [],
        "income-and-spam": [],
        "fees-and-failures": [],
        "ordering-duplicates-and-invalid-evidence": [],
    }
    prices: list[dict[str, object]] = []
    slot = 1_000

    def add_record(
        wallet: str,
        record_id: str,
        when: datetime,
        instructions: list[dict[str, object]],
        balances: list[dict[str, object]],
        *,
        scenario: str,
        signature: str | None = None,
        status: str = "succeeded",
        fee_quantity: int = 0,
        fee_charged: bool = False,
        fee_mint: str = SOL,
        forced_slot: int | None = None,
    ) -> dict[str, object]:
        nonlocal slot
        record: dict[str, object] = {
            "fixture_record_id": record_id,
            "signature": signature or f"synthetic_signature_{record_id}",
            "slot": forced_slot if forced_slot is not None else slot,
            "timestamp_utc": _timestamp(when),
            "status": status,
            "instruction_index": 0,
            "fee_base_units": fee_quantity,
            "fee_payer": wallet,
            "fee_charged": fee_charged,
            "instructions": instructions,
            "token_balance_changes": balances,
        }
        if fee_charged:
            record["fee_mint"] = fee_mint
        records[wallet].append(record)
        scenarios[scenario].append(record_id)
        slot += 1
        return record

    def add_price(price_id: str, mint: str, when: datetime, price: str) -> None:
        prices.append(
            {
                "price_id": price_id,
                "mint": mint,
                "timestamp_utc": _timestamp(when),
                "price_usd": price,
            }
        )

    old = datetime(2024, 1, 1, tzinfo=UTC)
    first = add_record(
        ALPHA,
        "complete-buy-old-skito",
        old,
        [_instruction("buy", SKITO, 4_000_000, program="synthetic_exchange", cash="8.00")],
        [_balance(SKITO, 4_000_000)],
        scenario="foundational-buys",
    )
    add_record(
        ALPHA,
        "complete-buy-sol-alpha",
        old + timedelta(days=1),
        [_instruction("buy", SOL, 1_000_000_000, program="synthetic_exchange", cash="100.00")],
        [_balance(SOL, 1_000_000_000)],
        scenario="foundational-buys",
    )
    add_record(
        BETA,
        "complete-buy-sol-beta",
        old + timedelta(days=2),
        [_instruction("buy", SOL, 500_000_000, program="synthetic_exchange", cash="50.00")],
        [_balance(SOL, 500_000_000)],
        scenario="foundational-buys",
    )
    for index in range(1, 12):
        when = old + timedelta(days=10 + index * 15)
        charged = index == 1
        record_id = f"complete-buy-alpha-skito-{index:02d}"
        add_record(
            ALPHA,
            record_id,
            when,
            [
                _instruction(
                    "buy",
                    SKITO,
                    10_000_000,
                    program="synthetic_exchange",
                    cash=f"{20 + index}.00",
                )
            ],
            [_balance(SKITO, 10_000_000)],
            scenario="foundational-buys",
            fee_quantity=10_000_000 if charged else 0,
            fee_charged=charged,
        )
        if charged:
            add_price("price_fee_acquisition", SOL, when, "100.00")
    tie_time = old + timedelta(days=240)
    add_record(
        ALPHA,
        "complete-buy-tie-zulu",
        tie_time,
        [_instruction("buy", SKITO, 2_000_000, program="synthetic_exchange", cash="6.00")],
        [_balance(SKITO, 2_000_000)],
        scenario="foundational-buys",
        signature="synthetic_signature_tie_zulu",
        forced_slot=5_000,
    )
    add_record(
        ALPHA,
        "complete-buy-tie-alpha",
        tie_time,
        [_instruction("buy", SKITO, 2_000_000, program="synthetic_exchange", cash="4.00")],
        [_balance(SKITO, 2_000_000)],
        scenario="foundational-buys",
        signature="synthetic_signature_tie_alpha",
        forced_slot=5_000,
    )
    for index in range(1, 4):
        when = old + timedelta(days=40 + index * 20)
        add_record(
            BETA,
            f"complete-buy-beta-skito-{index:02d}",
            when,
            [_instruction("buy", SKITO, 8_000_000, program="synthetic_exchange", cash="24.00")],
            [_balance(SKITO, 8_000_000)],
            scenario="foundational-buys",
        )
    add_record(
        BETA,
        "complete-buy-beta-mystery",
        old + timedelta(days=120),
        [_instruction("buy", MYST, 1_000_000, program="synthetic_exchange", cash="3.00")],
        [_balance(MYST, 1_000_000)],
        scenario="foundational-buys",
    )

    exact_boundary = datetime(2025, 1, 1, tzinfo=UTC)
    sale_times: list[datetime] = []
    for index in range(1, 17):
        when = exact_boundary + timedelta(days=index * 7)
        sale_times.append(when)
        wallet = ALPHA if index <= 12 else BETA
        quantity = 3_000_000 if index == 1 else 1_000_000
        charged = index == 3
        add_record(
            wallet,
            f"complete-sale-{index:02d}",
            exact_boundary if index == 1 else when,
            [
                _instruction(
                    "sell",
                    SKITO,
                    quantity,
                    program="synthetic_exchange",
                    cash=f"{quantity // 1_000_000 * 5}.00",
                )
            ],
            [_balance(SKITO, -quantity)],
            scenario="fifo-sales-and-holding",
            fee_quantity=10_000_000 if charged else 0,
            fee_charged=charged,
        )
        if charged:
            add_price("price_fee_disposal", SOL, when, "100.00")
    add_record(
        ALPHA,
        "complete-sale-after-boundary",
        exact_boundary + timedelta(seconds=1),
        [_instruction("sell", SKITO, 2_000_000, program="synthetic_exchange", cash="10.00")],
        [_balance(SKITO, -2_000_000)],
        scenario="fifo-sales-and-holding",
    )
    add_record(
        BETA,
        "complete-sale-insufficient",
        exact_boundary + timedelta(days=300),
        [_instruction("sell", SKITO, 999_000_000, program="synthetic_exchange", cash="999.00")],
        [_balance(SKITO, -999_000_000)],
        scenario="fifo-sales-and-holding",
    )

    transfer_time = datetime(2025, 5, 1, tzinfo=UTC)
    transfer_specs = [
        ("skito-span", ALPHA, BETA, SKITO, 5_000_000),
        ("skito-second", ALPHA, BETA, SKITO, 2_000_000),
        ("skito-return", BETA, ALPHA, SKITO, 1_000_000),
        ("skito-third", ALPHA, BETA, SKITO, 1_000_000),
        ("skito-return-two", BETA, ALPHA, SKITO, 1_000_000),
    ]
    for index, (name, source, destination, mint, quantity) in enumerate(transfer_specs):
        when = transfer_time + timedelta(days=index)
        signature = f"synthetic_signature_transfer_{name}"
        add_record(
            source,
            f"complete-transfer-{name}-out",
            when,
            [
                _instruction(
                    "transfer", mint, quantity, program="synthetic_system", counterparty=destination
                )
            ],
            [_balance(mint, -quantity)],
            scenario="owned-wallet-transfers",
            signature=signature,
        )
        add_record(
            destination,
            f"complete-transfer-{name}-in",
            when,
            [
                _instruction(
                    "transfer", mint, quantity, program="synthetic_system", counterparty=source
                )
            ],
            [_balance(mint, quantity)],
            scenario="owned-wallet-transfers",
            signature=signature,
        )

    external_base = datetime(2025, 4, 1, tzinfo=UTC)
    add_record(
        ALPHA,
        "complete-external-mystery-alpha",
        external_base,
        [
            _instruction(
                "transfer",
                MYST,
                2_000_000,
                program="synthetic_system",
                counterparty="synthetic_external_wallet_origin",
            )
        ],
        [_balance(MYST, 2_000_000)],
        scenario="external-and-unknown-basis",
    )
    mystery_transfer_time = transfer_time + timedelta(days=6)
    mystery_signature = "synthetic_signature_transfer_unknown_mystery"
    add_record(
        ALPHA,
        "complete-transfer-mystery-out",
        mystery_transfer_time,
        [_instruction("transfer", MYST, 1_000_000, program="synthetic_system", counterparty=BETA)],
        [_balance(MYST, -1_000_000)],
        scenario="owned-wallet-transfers",
        signature=mystery_signature,
    )
    add_record(
        BETA,
        "complete-transfer-mystery-in",
        mystery_transfer_time,
        [_instruction("transfer", MYST, 1_000_000, program="synthetic_system", counterparty=ALPHA)],
        [_balance(MYST, 1_000_000)],
        scenario="owned-wallet-transfers",
        signature=mystery_signature,
    )
    add_record(
        BETA,
        "complete-external-mystery-beta",
        external_base + timedelta(days=1),
        [
            _instruction(
                "transfer",
                MYST,
                1_000_000,
                program="synthetic_system",
                counterparty="synthetic_external_wallet_origin_two",
            )
        ],
        [_balance(MYST, 1_000_000)],
        scenario="external-and-unknown-basis",
    )
    for index in range(1, 7):
        wallet = ALPHA if index % 2 else BETA
        mint = DROP if index <= 3 else MYST
        quantity = index * 250_000
        add_record(
            wallet,
            f"complete-external-in-{index:02d}",
            external_base + timedelta(days=10 + index),
            [
                _instruction(
                    "transfer",
                    mint,
                    quantity,
                    program="synthetic_system",
                    counterparty=f"synthetic_external_wallet_source_{index:02d}",
                )
            ],
            [_balance(mint, quantity)],
            scenario="external-and-unknown-basis",
        )
    for index, wallet in enumerate((ALPHA, BETA), start=1):
        add_record(
            wallet,
            f"complete-external-out-{index:02d}",
            external_base + timedelta(days=30 + index),
            [
                _instruction(
                    "transfer",
                    SKITO,
                    1_000_000,
                    program="synthetic_system",
                    counterparty=f"synthetic_external_wallet_destination_{index:02d}",
                )
            ],
            [_balance(SKITO, -1_000_000)],
            scenario="external-and-unknown-basis",
        )
    add_record(
        BETA,
        "complete-sale-mixed-mystery",
        datetime(2025, 7, 1, tzinfo=UTC),
        [_instruction("sell", MYST, 2_500_000, program="synthetic_exchange", cash="12.50")],
        [_balance(MYST, -2_500_000)],
        scenario="external-and-unknown-basis",
    )

    swap_base = datetime(2025, 8, 1, tzinfo=UTC)
    for index in range(1, 9):
        wallet = ALPHA if index <= 6 else BETA
        when = swap_base + timedelta(days=index)
        record_id = f"complete-swap-valued-{index:02d}"
        add_record(
            wallet,
            record_id,
            when,
            [
                _instruction("swap", SKITO, 1_000_000, program="synthetic_swap_program"),
                _instruction("swap", NOVA, 2_000_000, program="synthetic_swap_program"),
            ],
            [_balance(SKITO, -1_000_000), _balance(NOVA, 2_000_000)],
            scenario="swaps-and-wraps",
        )
        add_price(f"price_{record_id}_skito", SKITO, when, "4.00")
        add_price(f"price_{record_id}_nova", NOVA, when, "2.00")
    for index in range(1, 4):
        when = swap_base + timedelta(days=20 + index)
        record_id = f"complete-swap-blocked-{index:02d}"
        add_record(
            ALPHA,
            record_id,
            when,
            [
                _instruction("swap", SKITO, 1_000_000, program="synthetic_swap_program"),
                _instruction("swap", NOVA, 2_000_000, program="synthetic_swap_program"),
            ],
            [_balance(SKITO, -1_000_000), _balance(NOVA, 2_000_000)],
            scenario="swaps-and-wraps",
        )
        if index == 1:
            add_price(f"price_{record_id}_skito", SKITO, when, "4.00")
        elif index == 2:
            add_price(f"price_{record_id}_skito_a", SKITO, when, "4.00")
            add_price(f"price_{record_id}_skito_b", SKITO, when, "4.50")
            add_price(f"price_{record_id}_nova", NOVA, when, "2.00")
    reverse_time = swap_base + timedelta(days=30)
    add_record(
        ALPHA,
        "complete-swap-reverse",
        reverse_time,
        [
            _instruction("swap", NOVA, 2_000_000, program="synthetic_swap_program"),
            _instruction("swap", SKITO, 1_000_000, program="synthetic_swap_program"),
        ],
        [_balance(NOVA, -2_000_000), _balance(SKITO, 1_000_000)],
        scenario="swaps-and-wraps",
    )
    add_price("price_complete_swap_reverse_nova", NOVA, reverse_time, "2.00")
    add_price("price_complete_swap_reverse_skito", SKITO, reverse_time, "4.00")

    wrap_time = datetime(2025, 9, 10, tzinfo=UTC)
    add_record(
        ALPHA,
        "complete-wrap-supported",
        wrap_time,
        [
            _instruction("wrap", SKITO, 2_000_000, program="synthetic_wrap_program"),
            _instruction("wrap", WSKITO, 2_000_000, program="synthetic_wrap_program"),
        ],
        [_balance(SKITO, -2_000_000), _balance(WSKITO, 2_000_000)],
        scenario="swaps-and-wraps",
    )
    add_record(
        BETA,
        "complete-wrap-ambiguous",
        wrap_time + timedelta(days=1),
        [
            _instruction("wrap", SKITO, 1_000_000, program="synthetic_wrap_program"),
            _instruction("wrap", WSKITO, 500_000, program="synthetic_wrap_program"),
        ],
        [_balance(SKITO, -1_000_000), _balance(WSKITO, 500_000)],
        scenario="swaps-and-wraps",
    )

    income_base = datetime(2025, 10, 1, tzinfo=UTC)
    for index in range(1, 5):
        when = income_base + timedelta(days=index)
        record_id = f"complete-reward-{index:02d}"
        add_record(
            ALPHA,
            record_id,
            when,
            [_instruction("staking_reward", RWD, 1_000_000, program="synthetic_staking_program")],
            [_balance(RWD, 1_000_000)],
            scenario="income-and-spam",
        )
        add_price(f"price_{record_id}", RWD, when, "2.00")
    for index in range(1, 4):
        when = income_base + timedelta(days=10 + index)
        record_id = f"complete-airdrop-{index:02d}"
        add_record(
            BETA,
            record_id,
            when,
            [_instruction("airdrop", DROP, 2_000_000, program="synthetic_airdrop_program")],
            [_balance(DROP, 2_000_000)],
            scenario="income-and-spam",
        )
        add_price(f"price_{record_id}", DROP, when, "0.50")
    for index in range(1, 4):
        when = income_base + timedelta(days=20 + index)
        wallet = ALPHA if index % 2 else BETA
        add_record(
            wallet,
            f"complete-spam-{index:02d}",
            when,
            [_instruction("spam_airdrop", SPAM, index, program="synthetic_airdrop_program")],
            [_balance(SPAM, index)],
            scenario="income-and-spam",
        )

    fee_base = datetime(2025, 11, 1, tzinfo=UTC)
    for index, (wallet, status, charged) in enumerate(
        (
            (ALPHA, "succeeded", True),
            (ALPHA, "failed", True),
            (ALPHA, "failed", False),
            (BETA, "succeeded", True),
            (BETA, "failed", True),
            (ALPHA, "failed", True),
        ),
        start=1,
    ):
        when = fee_base + timedelta(days=index)
        record_id = f"complete-fee-failure-{index:02d}"
        instructions = []
        balances: list[dict[str, object]] = []
        if status == "failed":
            instructions = [
                _instruction("swap", SKITO, 1_000_000, program="synthetic_swap_program"),
                _instruction("swap", NOVA, 2_000_000, program="synthetic_swap_program"),
            ]
            balances = [_balance(SKITO, -1_000_000), _balance(NOVA, 2_000_000)]
        add_record(
            wallet,
            record_id,
            when,
            instructions,
            balances,
            scenario="fees-and-failures",
            status=status,
            fee_quantity=10_000_000 if charged else 0,
            fee_charged=charged,
        )
        if charged:
            add_price(f"price_{record_id}_sol", SOL, when, "100.00")

    duplicate = dict(first)
    duplicate["fixture_record_id"] = "complete-buy-old-skito-copy"
    records[ALPHA].append(duplicate)
    scenarios["ordering-duplicates-and-invalid-evidence"].append("complete-buy-old-skito-copy")
    conflict_time = datetime(2025, 12, 1, tzinfo=UTC)
    conflict_signature = "synthetic_signature_conflicting_pair"
    for suffix, quantity in (("a", 1_000_000), ("b", 2_000_000)):
        add_record(
            ALPHA,
            f"complete-conflict-{suffix}",
            conflict_time,
            [
                _instruction(
                    "transfer",
                    SKITO,
                    quantity,
                    program="synthetic_system",
                    counterparty="synthetic_external_wallet_conflict",
                )
            ],
            [_balance(SKITO, -quantity)],
            scenario="ordering-duplicates-and-invalid-evidence",
            signature=conflict_signature,
            forced_slot=9_000,
        )
    add_record(
        ALPHA,
        "complete-instruction-balance-conflict",
        conflict_time + timedelta(days=1),
        [
            _instruction(
                "transfer",
                SKITO,
                2_000_000,
                program="synthetic_system",
                counterparty="synthetic_external_wallet_bad_balance",
            )
        ],
        [_balance(SKITO, -1_000_000)],
        scenario="ordering-duplicates-and-invalid-evidence",
    )
    add_record(
        BETA,
        "complete-missing-token-metadata",
        conflict_time + timedelta(days=2),
        [
            _instruction(
                "transfer",
                "synthetic_mint_missing_metadata",
                1_000_000,
                program="synthetic_system",
                counterparty="synthetic_external_wallet_unknown_token",
            )
        ],
        [_balance("synthetic_mint_missing_metadata", 1_000_000)],
        scenario="ordering-duplicates-and-invalid-evidence",
    )

    total_records = sum(len(items) for items in records.values())
    if total_records != 96:
        raise AssertionError(f"Expected 96 stored records, built {total_records}.")

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(
        FIXTURE_DIR / "manifest.json",
        {
            "contract_version": 1,
            "fixture_set_id": FIXTURE_SET_ID,
            "wallet_files": ["wallets/wallet_alpha.json", "wallets/wallet_beta.json"],
            "ownership_file": "ownership.json",
            "tokens_file": "tokens.json",
            "prices_file": "prices.json",
            "scenario_catalog_file": "scenario-catalog.json",
            "ground_truth_dir": "ground_truth",
        },
    )
    _write_json(
        FIXTURE_DIR / "ownership.json",
        {
            "fixture_set_id": FIXTURE_SET_ID,
            "client_id": "synthetic_client_final_assessment",
            "owned_wallet_ids": [ALPHA, BETA],
        },
    )
    symbols = {
        SKITO: "SKITO",
        NOVA: "NOVA",
        SOL: "SOL",
        RWD: "RWD",
        DROP: "DROP",
        SPAM: "SPAM",
        MYST: "MYST",
        WSKITO: "wSKITO",
    }
    _write_json(
        FIXTURE_DIR / "tokens.json",
        {
            "fixture_set_id": FIXTURE_SET_ID,
            "tokens": [
                {"mint": mint, "symbol": symbols[mint], "decimals": decimals}
                for mint, decimals in TOKEN_DECIMALS.items()
            ],
        },
    )
    _write_json(
        FIXTURE_DIR / "prices.json",
        {
            "fixture_set_id": FIXTURE_SET_ID,
            "valuation_currency": "USD",
            "prices": prices,
        },
    )
    scenario_descriptions = {
        "foundational-buys": (
            "Create exact known-basis SKITO, SOL, and MYST inventory in both wallets.",
            "Collapsing acquisitions would lose dates, basis, and deterministic tie evidence.",
            "Every accepted buy creates a distinct source-linked FIFO lot.",
        ),
        "fifo-sales-and-holding": (
            "Exercise partial, multi-lot, fee-adjusted, holding-boundary, and overdrawn sales.",
            "A naive average basis or greater-than-or-equal holding rule changes report rows.",
            "Complete slices report separately and impossible inventory is excepted.",
        ),
        "owned-wallet-transfers": (
            "Move known and unknown basis slices between the two owned wallets.",
            "Treating wallet views independently duplicates or disposes inventory.",
            "Matched views create one movement with original basis and date lineage.",
        ),
        "external-and-unknown-basis": (
            "Preserve unknown inbound basis, mixed disposals, and outbound suspense.",
            "Using zero basis or ignoring suspense creates unsupported monetary output.",
            "Known slices report while unknown dependencies remain exception-linked.",
        ),
        "swaps-and-wraps": (
            "Cover valued and blocked swaps plus supported and ambiguous wrapping.",
            "One-sided prices or assumed wrap equivalence fabricate basis continuity.",
            "Exact agreeing swaps report; unsupported values and wraps remain explicit.",
        ),
        "income-and-spam": (
            "Recognize exact reward and airdrop income while quarantining spam.",
            "Treating every receipt as zero or valid income misstates both income and inventory.",
            "Valued income equals created-lot basis; spam creates no income row.",
        ),
        "fees-and-failures": (
            "Exercise fee-only and failed events with charged and uncharged evidence.",
            "Processing reverted legs or dropping charged fees changes inventory.",
            "Only proven charged fees survive failed transactions.",
        ),
        "ordering-duplicates-and-invalid-evidence": (
            "Prove stable ties, identical duplicates, conflicts, and record quarantine.",
            "File-order selection would double count or accept contradictory facts.",
            "Coverage marks every record resolved, secondary, or quarantined.",
        ),
    }
    _write_json(
        FIXTURE_DIR / "scenario-catalog.json",
        {
            "fixture_set_id": FIXTURE_SET_ID,
            "scenarios": [
                {
                    "scenario_id": scenario_id,
                    "source_record_ids": record_ids,
                    "purpose": scenario_descriptions[scenario_id][0],
                    "naive_failure": scenario_descriptions[scenario_id][1],
                    "expected_outcome": scenario_descriptions[scenario_id][2],
                }
                for scenario_id, record_ids in scenarios.items()
            ],
        },
    )
    for wallet, items in records.items():
        name = "wallet_alpha.json" if wallet == ALPHA else "wallet_beta.json"
        _write_json(
            FIXTURE_DIR / "wallets" / name,
            {"fixture_set_id": FIXTURE_SET_ID, "wallet_id": wallet, "records": items},
        )

    ground_truth = FIXTURE_DIR / "ground_truth"
    if (ground_truth / "metadata.json").exists():
        return
    _write_json(
        ground_truth / "metadata.json",
        {
            "contract_version": 1,
            "fixture_set_id": FIXTURE_SET_ID,
            "policy_id": "us_federal_assessment_fifo",
            "policy_version": 1,
            "valuation_currency": "USD",
        },
    )
    for name, key in (
        ("classifications.json", "classifications"),
        ("lots.json", "lots"),
        ("consumptions.json", "consumptions"),
        ("lot_movements.json", "lot_movements"),
        ("ending_inventory.json", "ending_inventory"),
        ("reconciliation.json", "reconciliation"),
    ):
        _write_json(
            ground_truth / name,
            {"fixture_set_id": FIXTURE_SET_ID, key: [] if key != "reconciliation" else {}},
        )
    _write_json(
        ground_truth / "expected_exceptions.json",
        {
            "contract_version": 1,
            "fixture_set_id": FIXTURE_SET_ID,
            "policy_id": "us_federal_assessment_fifo",
            "policy_version": 1,
            "jurisdiction": "US",
            "valuation_currency": "USD",
            "basis_method": "FIFO",
            "basis_scope": "wallet_and_mint",
            "price_rule": "exact_timestamp",
            "swap_valuation_rule": "require_agreeing_legs",
            "holding_term_rule": "more_than_one_year",
            "partial_output_rule": "report_complete_slices",
            "outbound_rule": "suspense",
            "spam_rule": "quarantine_unpriced",
            "fee_rule": "event_context",
            "rounding_rule": "half_even",
            "report_decimal_places": 2,
            "exceptions": [],
        },
    )
    for name, columns in (
        ("expected_disposals.csv", ["placeholder"]),
        ("expected_income.csv", ["placeholder"]),
    ):
        path = ground_truth / name
        with path.open("w", encoding="utf-8", newline="") as output_file:
            csv.writer(output_file, lineterminator="\n").writerow(columns)

if __name__ == "__main__":
    main()
