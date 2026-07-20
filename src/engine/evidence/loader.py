"""Load local fixture evidence and quarantine isolatable record errors."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, cast

from engine.core.exceptions import ExceptionCollector
from engine.core.identity import derive_source_id
from engine.core.values import ExactValueError, parse_base_units, parse_decimal_string
from engine.models import (
    ExceptionSeverity,
    FeeEvidence,
    FixtureBundle,
    GroundTruthMetadata,
    ObservedFact,
    PipelineStage,
    PricePoint,
    RawInstructionEvidence,
    RawTokenBalanceChange,
    ScenarioDefinition,
    SourceReference,
    TokenMetadata,
    Transaction,
    TransactionStatus,
)
from engine.policy.config import (
    CONTRACT_VERSION,
    AssessmentPolicy,
    require_supported_contract,
    require_supported_policy,
)


class FixtureValidationError(ValueError):
    """Raised when a file-level fixture problem makes the run unsafe."""

def _reject_float(value: str) -> NoReturn:
    raise FixtureValidationError(f"JSON binary-float input is forbidden: {value}.")

def _reject_constant(value: str) -> NoReturn:
    raise FixtureValidationError(f"Non-finite JSON input is forbidden: {value}.")

def _read_object(path: Path) -> dict[str, object]:
    try:
        raw: object = json.loads(
            path.read_text(encoding="utf-8"),
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except FileNotFoundError as error:
        raise FixtureValidationError(f"Required fixture file is missing: {path}.") from error
    except json.JSONDecodeError as error:
        raise FixtureValidationError(f"Fixture file is invalid JSON: {path}: {error}.") from error
    return _as_object(raw, field=str(path))

def _as_object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise FixtureValidationError(f"{field} must be a JSON object.")
    return cast(dict[str, object], value)

def _as_list(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise FixtureValidationError(f"{field} must be a JSON array.")
    return cast(list[object], value)

def _as_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise FixtureValidationError(f"{field} must be a non-empty string.")
    return value

def _optional_string(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    return _as_string(value, field=field)

def _as_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise FixtureValidationError(f"{field} must be true or false.")
    return value

def _parse_timestamp(value: object, *, field: str) -> datetime:
    timestamp = _as_string(value, field=field)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise FixtureValidationError(f"{field} must be an ISO UTC timestamp.") from error
    if parsed.tzinfo != UTC:
        raise FixtureValidationError(f"{field} must use UTC, got {timestamp!r}.")
    return parsed

def _fixture_path(root: Path, relative: object, *, field: str) -> Path:
    relative_path = Path(_as_string(relative, field=field))
    resolved_root = root.resolve()
    resolved_path = (root / relative_path).resolve()
    if resolved_path != resolved_root and resolved_root not in resolved_path.parents:
        raise FixtureValidationError(f"{field} leaves the fixture directory: {relative_path}.")
    return resolved_path

def _load_tokens(path: Path, *, fixture_set_id: str) -> tuple[TokenMetadata, ...]:
    payload = _read_object(path)
    if payload.get("fixture_set_id") != fixture_set_id:
        raise FixtureValidationError(f"Token metadata fixture_set_id differs in {path}.")
    tokens: list[TokenMetadata] = []
    for index, value in enumerate(_as_list(payload.get("tokens"), field="tokens")):
        item = _as_object(value, field=f"tokens[{index}]")
        mint = _as_string(item.get("mint"), field=f"tokens[{index}].mint")
        if not mint.startswith("synthetic_mint_"):
            raise FixtureValidationError(f"Token mint is not visibly synthetic: {mint}.")
        tokens.append(
            TokenMetadata(
                mint=mint,
                symbol=_as_string(item.get("symbol"), field=f"tokens[{index}].symbol"),
                decimals=parse_base_units(
                    item.get("decimals"), field_name=f"tokens[{index}].decimals"
                ),
            )
        )
    if len({token.mint for token in tokens}) != len(tokens):
        raise FixtureValidationError("Token metadata contains a duplicate mint.")
    return tuple(sorted(tokens, key=lambda token: token.mint))

def _load_ownership(path: Path, *, fixture_set_id: str) -> tuple[str, tuple[str, ...]]:
    payload = _read_object(path)
    if payload.get("fixture_set_id") != fixture_set_id:
        raise FixtureValidationError(f"Ownership fixture_set_id differs in {path}.")
    client_id = _as_string(payload.get("client_id"), field="client_id")
    wallet_ids = tuple(
        _as_string(value, field="owned_wallet_ids[]")
        for value in _as_list(payload.get("owned_wallet_ids"), field="owned_wallet_ids")
    )
    if not client_id.startswith("synthetic_client_"):
        raise FixtureValidationError(f"Client ID is not visibly synthetic: {client_id}.")
    if len(wallet_ids) != 2 or len(set(wallet_ids)) != 2:
        raise FixtureValidationError("Ownership must name exactly two distinct wallets.")
    if any(not wallet_id.startswith("synthetic_wallet_") for wallet_id in wallet_ids):
        raise FixtureValidationError("Every owned wallet ID must be visibly synthetic.")
    return client_id, tuple(sorted(wallet_ids))

def _load_prices(
    path: Path, *, fixture_set_id: str, policy: AssessmentPolicy
) -> tuple[PricePoint, ...]:
    payload = _read_object(path)
    if payload.get("fixture_set_id") != fixture_set_id:
        raise FixtureValidationError(f"Price fixture_set_id differs in {path}.")
    if payload.get("valuation_currency") != policy.valuation_currency.value:
        raise FixtureValidationError("Price currency differs from the assessment policy.")
    valuation_currency = policy.valuation_currency.value
    prices: list[PricePoint] = []
    for index, value in enumerate(_as_list(payload.get("prices"), field="prices")):
        item = _as_object(value, field=f"prices[{index}]")
        prices.append(
            PricePoint(
                price_id=_as_string(item.get("price_id"), field=f"prices[{index}].price_id"),
                mint=_as_string(item.get("mint"), field=f"prices[{index}].mint"),
                timestamp_utc=_parse_timestamp(
                    item.get("timestamp_utc"), field=f"prices[{index}].timestamp_utc"
                ),
                valuation_currency=valuation_currency,
                price_usd=parse_decimal_string(
                    item.get("price_usd"), field_name=f"prices[{index}].price_usd"
                ),
            )
        )
    if len({point.price_id for point in prices}) != len(prices):
        raise FixtureValidationError("Price evidence contains a duplicate price ID.")
    return tuple(
        sorted(prices, key=lambda point: (point.timestamp_utc, point.mint, point.price_id))
    )

def _load_scenarios(path: Path, *, fixture_set_id: str) -> tuple[ScenarioDefinition, ...]:
    payload = _read_object(path)
    if payload.get("fixture_set_id") != fixture_set_id:
        raise FixtureValidationError(f"Scenario fixture_set_id differs in {path}.")
    scenarios: list[ScenarioDefinition] = []
    for index, value in enumerate(_as_list(payload.get("scenarios"), field="scenarios")):
        item = _as_object(value, field=f"scenarios[{index}]")
        scenarios.append(
            ScenarioDefinition(
                scenario_id=_as_string(
                    item.get("scenario_id"), field=f"scenarios[{index}].scenario_id"
                ),
                source_record_ids=tuple(
                    _as_string(record_id, field=f"scenarios[{index}].source_record_ids[]")
                    for record_id in _as_list(
                        item.get("source_record_ids"),
                        field=f"scenarios[{index}].source_record_ids",
                    )
                ),
                purpose=_as_string(item.get("purpose"), field=f"scenarios[{index}].purpose"),
                naive_failure=_as_string(
                    item.get("naive_failure"), field=f"scenarios[{index}].naive_failure"
                ),
                expected_outcome=_as_string(
                    item.get("expected_outcome"), field=f"scenarios[{index}].expected_outcome"
                ),
            )
        )
    return tuple(sorted(scenarios, key=lambda scenario: scenario.scenario_id))

def _load_ground_truth(
    path: Path, *, fixture_set_id: str, policy: AssessmentPolicy
) -> GroundTruthMetadata:
    metadata = _read_object(path / "metadata.json")
    require_supported_contract(actual=metadata.get("contract_version"), name="ground truth")
    policy_version = parse_base_units(
        metadata.get("policy_version"), field_name="ground_truth.policy_version"
    )
    result = GroundTruthMetadata(
        contract_version=CONTRACT_VERSION,
        fixture_set_id=_as_string(
            metadata.get("fixture_set_id"), field="ground_truth.fixture_set_id"
        ),
        policy_id=_as_string(metadata.get("policy_id"), field="ground_truth.policy_id"),
        policy_version=policy_version,
        valuation_currency=_as_string(
            metadata.get("valuation_currency"), field="ground_truth.valuation_currency"
        ),
    )
    if result.fixture_set_id != fixture_set_id:
        raise FixtureValidationError("Ground truth names a different fixture set.")
    if (
        result.policy_id != policy.policy_id
        or result.policy_version != policy.policy_version
        or result.valuation_currency != policy.valuation_currency.value
    ):
        raise FixtureValidationError("Ground truth policy metadata differs from the run policy.")
    for name in ("classifications.json", "lots.json", "consumptions.json", "reconciliation.json"):
        _read_object(path / name)
    exceptions = _read_object(path / "expected_exceptions.json")
    require_supported_contract(
        actual=exceptions.get("contract_version"), name="exception output ground truth"
    )
    for name in ("expected_disposals.csv", "expected_income.csv"):
        try:
            with (path / name).open(encoding="utf-8", newline="") as expected_file:
                if next(csv.reader(expected_file), None) is None:
                    raise FixtureValidationError(f"Ground-truth CSV is empty: {name}.")
        except FileNotFoundError as error:
            raise FixtureValidationError(
                f"Required ground-truth file is missing: {name}."
            ) from error
    return result

def _load_instruction(value: object, *, record_field: str) -> RawInstructionEvidence:
    item = _as_object(value, field=f"{record_field}.instructions[]")
    cash_value = item.get("cash_value_usd")
    return RawInstructionEvidence(
        program=_as_string(item.get("program"), field=f"{record_field}.program"),
        instruction_type=_as_string(
            item.get("instruction_type"), field=f"{record_field}.instruction_type"
        ),
        mint=_as_string(item.get("mint"), field=f"{record_field}.mint"),
        quantity_base_units=parse_base_units(
            item.get("quantity_base_units"), field_name=f"{record_field}.quantity_base_units"
        ),
        cash_value_usd=(
            None
            if cash_value is None
            else parse_decimal_string(cash_value, field_name=f"{record_field}.cash_value_usd")
        ),
        counterparty_wallet_id=_optional_string(
            item.get("counterparty_wallet_id"), field=f"{record_field}.counterparty_wallet_id"
        ),
    )

def _load_balance(value: object, *, record_field: str) -> RawTokenBalanceChange:
    item = _as_object(value, field=f"{record_field}.token_balance_changes[]")
    pre = parse_base_units(
        item.get("pre_balance_base_units"), field_name=f"{record_field}.pre_balance"
    )
    post = parse_base_units(
        item.get("post_balance_base_units"), field_name=f"{record_field}.post_balance"
    )
    delta = parse_base_units(
        item.get("delta_base_units"),
        field_name=f"{record_field}.delta",
        allow_negative=True,
    )
    if post - pre != delta:
        raise FixtureValidationError(
            f"{record_field} balance conflict: {post} - {pre} does not equal {delta}."
        )
    return RawTokenBalanceChange(
        mint=_as_string(item.get("mint"), field=f"{record_field}.mint"),
        pre_balance_base_units=pre,
        post_balance_base_units=post,
        delta_base_units=delta,
    )

def _load_wallet(
    path: Path,
    *,
    fixture_set_id: str,
    token_metadata: dict[str, TokenMetadata],
    collector: ExceptionCollector,
) -> tuple[tuple[Transaction, ...], tuple[SourceReference, ...]]:
    payload = _read_object(path)
    if payload.get("fixture_set_id") != fixture_set_id:
        raise FixtureValidationError(f"Wallet fixture_set_id differs in {path}.")
    wallet_id = _as_string(payload.get("wallet_id"), field=f"{path}.wallet_id")
    if not wallet_id.startswith("synthetic_wallet_"):
        raise FixtureValidationError(f"Wallet ID is not visibly synthetic: {wallet_id}.")

    transactions: list[Transaction] = []
    source_refs: list[SourceReference] = []
    records_field = f"{path.name}.records"
    for index, value in enumerate(_as_list(payload.get("records"), field=records_field)):
        field = f"{records_field}[{index}]"
        item = _as_object(value, field=field)
        fixture_record_id = _as_string(
            item.get("fixture_record_id"), field=f"{field}.fixture_record_id"
        )
        signature = _as_string(item.get("signature"), field=f"{field}.signature")
        slot = parse_base_units(item.get("slot"), field_name=f"{field}.slot")
        instruction_index = parse_base_units(
            item.get("instruction_index"), field_name=f"{field}.instruction_index"
        )
        source = SourceReference(
            source_id=derive_source_id(
                fixture_set_id=fixture_set_id,
                wallet_id=wallet_id,
                fixture_record_id=fixture_record_id,
                signature=signature,
                slot=slot,
                instruction_index=instruction_index,
            ),
            fixture_set_id=fixture_set_id,
            wallet_id=wallet_id,
            fixture_record_id=fixture_record_id,
            signature=signature,
        )
        source_refs.append(source)
        try:
            status = TransactionStatus(_as_string(item.get("status"), field=f"{field}.status"))
            fee_base_units = parse_base_units(
                item.get("fee_base_units"), field_name=f"{field}.fee_base_units"
            )
            fee_charged = _as_bool(item.get("fee_charged"), field=f"{field}.fee_charged")
            fee_mint = (
                _as_string(item.get("fee_mint"), field=f"{field}.fee_mint") if fee_charged else None
            )
            fee_token = token_metadata.get(fee_mint) if fee_mint is not None else None
            if fee_charged and fee_token is None:
                raise FixtureValidationError(
                    f"{field}.fee_mint has no matching token metadata: {fee_mint}."
                )
            transaction = Transaction(
                source=source,
                slot=slot,
                timestamp_utc=_parse_timestamp(
                    item.get("timestamp_utc"), field=f"{field}.timestamp_utc"
                ),
                status=status,
                instruction_index=instruction_index,
                fee=FeeEvidence(
                    payer_wallet_id=_as_string(item.get("fee_payer"), field=f"{field}.fee_payer"),
                    fee_base_units=fee_base_units,
                    charged=fee_charged,
                    mint=fee_token.mint if fee_token is not None else None,
                    symbol=fee_token.symbol if fee_token is not None else None,
                    decimals=fee_token.decimals if fee_token is not None else None,
                ),
                instructions=tuple(
                    _load_instruction(instruction, record_field=field)
                    for instruction in _as_list(
                        item.get("instructions"), field=f"{field}.instructions"
                    )
                ),
                token_balance_changes=tuple(
                    _load_balance(balance, record_field=field)
                    for balance in _as_list(
                        item.get("token_balance_changes"),
                        field=f"{field}.token_balance_changes",
                    )
                ),
            )
        except (FixtureValidationError, ExactValueError, ValueError) as error:
            collector.add(
                code="invalid_source_record",
                severity=ExceptionSeverity.EVENT_BLOCKING,
                stage=PipelineStage.LOADING,
                source_refs=(source,),
                observed_facts=(ObservedFact(name="validation_error", value=str(error)),),
                reason="The record has isolatable contradictory or malformed evidence.",
                affected_outputs=("disposals.csv", "income.csv", "exceptions.json"),
                related_object_ids=(),
                human_request="Correct the named source fields and provide reconciled evidence.",
            )
            continue
        transactions.append(transaction)
    return tuple(transactions), tuple(source_refs)

def load_fixture(fixtures_dir: Path, *, policy: AssessmentPolicy) -> FixtureBundle:
    """Load one fixture set while preserving valid work around quarantined records."""
    require_supported_policy(policy)
    manifest = _read_object(fixtures_dir / "manifest.json")
    require_supported_contract(actual=manifest.get("contract_version"), name="fixture set")
    fixture_set_id = _as_string(manifest.get("fixture_set_id"), field="fixture_set_id")
    collector = ExceptionCollector()

    ownership_path = _fixture_path(
        fixtures_dir, manifest.get("ownership_file"), field="ownership_file"
    )
    tokens_path = _fixture_path(fixtures_dir, manifest.get("tokens_file"), field="tokens_file")
    prices_path = _fixture_path(fixtures_dir, manifest.get("prices_file"), field="prices_file")
    catalog_path = _fixture_path(
        fixtures_dir, manifest.get("scenario_catalog_file"), field="scenario_catalog_file"
    )
    ground_truth_path = _fixture_path(
        fixtures_dir, manifest.get("ground_truth_dir"), field="ground_truth_dir"
    )
    client_id, owned_wallet_ids = _load_ownership(ownership_path, fixture_set_id=fixture_set_id)
    tokens = _load_tokens(tokens_path, fixture_set_id=fixture_set_id)
    prices = _load_prices(prices_path, fixture_set_id=fixture_set_id, policy=policy)
    scenarios = _load_scenarios(catalog_path, fixture_set_id=fixture_set_id)
    ground_truth = _load_ground_truth(
        ground_truth_path, fixture_set_id=fixture_set_id, policy=policy
    )

    transactions: list[Transaction] = []
    all_source_refs: list[SourceReference] = []
    for wallet_file in _as_list(manifest.get("wallet_files"), field="wallet_files"):
        loaded, source_refs = _load_wallet(
            _fixture_path(fixtures_dir, wallet_file, field="wallet_files[]"),
            fixture_set_id=fixture_set_id,
            token_metadata={token.mint: token for token in tokens},
            collector=collector,
        )
        transactions.extend(loaded)
        all_source_refs.extend(source_refs)
    if {source.wallet_id for source in all_source_refs} - set(owned_wallet_ids):
        raise FixtureValidationError("A wallet history is missing from the ownership fixture.")
    mapped_record_ids = {
        record_id for scenario in scenarios for record_id in scenario.source_record_ids
    }
    actual_record_ids = {source.fixture_record_id for source in all_source_refs}
    if mapped_record_ids != actual_record_ids:
        missing = sorted(actual_record_ids - mapped_record_ids)
        unknown = sorted(mapped_record_ids - actual_record_ids)
        raise FixtureValidationError(
            f"Scenario coverage differs from wallet records; missing={missing}, unknown={unknown}."
        )
    transactions.sort(
        key=lambda item: (
            item.timestamp_utc,
            item.slot,
            item.source.signature,
            item.instruction_index,
            item.source.wallet_id,
            item.source.fixture_record_id,
        )
    )
    return FixtureBundle(
        fixture_set_id=fixture_set_id,
        client_id=client_id,
        owned_wallet_ids=owned_wallet_ids,
        tokens=tokens,
        prices=prices,
        scenarios=scenarios,
        ground_truth_metadata=ground_truth,
        transactions=tuple(transactions),
        all_source_refs=tuple(sorted(all_source_refs, key=lambda source: source.fixture_record_id)),
        load_exceptions=collector.items(),
    )
