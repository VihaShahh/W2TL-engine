"""Read bounded public Solana history into the canonical raw evidence contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from engine.core.identity import derive_source_id
from engine.models import (
    FeeEvidence,
    RawInstructionEvidence,
    RawTokenBalanceChange,
    SourceReference,
    TokenMetadata,
    Transaction,
    TransactionStatus,
)

DEFAULT_PUBLIC_SOLANA_ENDPOINT = "https://api.mainnet-beta.solana.com"
PUBLIC_OBSERVATION_CONTRACT_VERSION = 1
PUBLIC_FIXTURE_SET_ID = "public_solana_observation_v1"
NATIVE_SOL_MINT = "So11111111111111111111111111111111111111112"
BASE58_ALPHABET = frozenset("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")

RpcTransport = Callable[[str, dict[str, object]], object]

class PublicSolanaAdapterError(ValueError):
    """Raised when public request configuration or RPC structure is unsafe."""

class PublicSolanaRateLimitError(PublicSolanaAdapterError):
    """Raised by a transport when the public endpoint returns HTTP 429."""

@dataclass(frozen=True, slots=True)
class PublicRetrievalIssue:
    code: str
    signature: str | None
    detail: str

@dataclass(frozen=True, slots=True)
class PublicSolanaProvenance:
    endpoint: str
    public_wallet_address: str
    ownership_status: str
    commitment: str
    page_limit: int
    max_pages: int
    pages_retrieved: int
    transaction_requests: int
    newest_signature: str | None
    oldest_signature: str | None
    pagination_complete: bool
    history_complete: bool
    stopped_reason: str
    response_sha256: tuple[str, ...]

@dataclass(frozen=True, slots=True)
class PublicSolanaObservation:
    provenance: PublicSolanaProvenance
    tokens: tuple[TokenMetadata, ...]
    transactions: tuple[Transaction, ...]
    issues: tuple[PublicRetrievalIssue, ...]

def _validate_address(value: str) -> str:
    if not isinstance(value, str) or not 32 <= len(value) <= 44 or set(value) - BASE58_ALPHABET:
        raise PublicSolanaAdapterError("Wallet address must be a public base58 Solana address.")
    return value

def _validate_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise PublicSolanaAdapterError(
            "Endpoint must be credential-free HTTPS with no user info, query, or fragment."
        )
    return value

def _http_transport(endpoint: str, payload: dict[str, object]) -> object:
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "W2TL-public-observation/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 429:
            raise PublicSolanaRateLimitError(
                "Public Solana endpoint rate limited the request."
            ) from error
        raise PublicSolanaAdapterError(
            f"Public Solana endpoint returned HTTP {error.code}."
        ) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise PublicSolanaAdapterError("Public Solana endpoint request failed.") from error

def _object(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PublicSolanaAdapterError(f"RPC {field} must be an object.")
    return cast(dict[str, object], value)

def _array(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise PublicSolanaAdapterError(f"RPC {field} must be an array.")
    return cast(list[object], value)

def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PublicSolanaAdapterError(f"RPC {field} must be a non-empty string.")
    return value

def _integer(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicSolanaAdapterError(f"RPC {field} must be a non-negative integer.")
    return value

def _rpc_result(response: object, *, method: str) -> object:
    payload = _object(response, field=f"{method} response")
    error = payload.get("error")
    if error is not None:
        detail = _object(error, field=f"{method}.error")
        raise PublicSolanaAdapterError(
            f"{method} RPC error {detail.get('code')}: {detail.get('message')}."
        )
    if "result" not in payload:
        raise PublicSolanaAdapterError(f"{method} response has no result.")
    return payload["result"]

def _response_hash(response: object) -> str:
    encoded = json.dumps(
        response, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()

def _request(
    *, method: str, params: list[object], request_id: int
) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }

def _parsed_system_transfer(
    *,
    address: str,
    signature: str,
    signature_slot: int,
    signature_error: object,
    result: object,
) -> Transaction | None:
    transaction_result = _object(result, field="getTransaction.result")
    message = _object(
        _object(transaction_result.get("transaction"), field="transaction").get("message"),
        field="transaction.message",
    )
    instructions = _array(message.get("instructions"), field="message.instructions")
    matches: list[tuple[int, dict[str, object]]] = []
    for index, value in enumerate(instructions):
        instruction = _object(value, field="message.instructions[]")
        if instruction.get("program") != "system":
            continue
        parsed = instruction.get("parsed")
        if not isinstance(parsed, dict):
            continue
        parsed_object = _object(parsed, field="instruction.parsed")
        if parsed_object.get("type") != "transfer":
            continue
        info = _object(parsed_object.get("info"), field="instruction.parsed.info")
        if address in {info.get("source"), info.get("destination")}:
            matches.append((index, info))
    if len(matches) != 1:
        return None
    instruction_index, info = matches[0]
    source = _string(info.get("source"), field="transfer.source")
    destination = _string(info.get("destination"), field="transfer.destination")
    lamports = _integer(info.get("lamports"), field="transfer.lamports")
    if lamports <= 0:
        raise PublicSolanaAdapterError("Supported public transfer must have positive lamports.")
    meta = _object(transaction_result.get("meta"), field="getTransaction.meta")
    fee = _integer(meta.get("fee"), field="getTransaction.meta.fee")
    account_keys = _array(message.get("accountKeys"), field="message.accountKeys")
    if not account_keys:
        raise PublicSolanaAdapterError("Public transaction has no account keys.")
    first_key = account_keys[0]
    fee_payer = (
        _string(_object(first_key, field="accountKeys[0]").get("pubkey"), field="fee_payer")
        if isinstance(first_key, dict)
        else _string(first_key, field="fee_payer")
    )
    outbound = source == address
    delta = -lamports if outbound else lamports
    block_time = transaction_result.get("blockTime")
    if block_time is None:
        return None
    timestamp = datetime.fromtimestamp(_integer(block_time, field="blockTime"), tz=UTC)
    slot = _integer(transaction_result.get("slot"), field="getTransaction.slot")
    if slot != signature_slot:
        raise PublicSolanaAdapterError("Signature and transaction slots conflict.")
    source_id = derive_source_id(
        fixture_set_id=PUBLIC_FIXTURE_SET_ID,
        wallet_id=address,
        fixture_record_id=signature,
        signature=signature,
        slot=slot,
        instruction_index=instruction_index,
    )
    failed = signature_error is not None or meta.get("err") is not None
    return Transaction(
        source=SourceReference(
            source_id=source_id,
            fixture_set_id=PUBLIC_FIXTURE_SET_ID,
            wallet_id=address,
            fixture_record_id=signature,
            signature=signature,
        ),
        slot=slot,
        timestamp_utc=timestamp,
        status=TransactionStatus.FAILED if failed else TransactionStatus.SUCCEEDED,
        instruction_index=instruction_index,
        fee=FeeEvidence(
            payer_wallet_id=fee_payer,
            fee_base_units=fee,
            charged=fee > 0,
            mint=NATIVE_SOL_MINT if fee > 0 else None,
            symbol="SOL" if fee > 0 else None,
            decimals=9 if fee > 0 else None,
        ),
        instructions=(
            RawInstructionEvidence(
                program="system",
                instruction_type="transfer",
                mint=NATIVE_SOL_MINT,
                quantity_base_units=lamports,
                cash_value_usd=None,
                counterparty_wallet_id=destination if outbound else source,
            ),
        ),
        token_balance_changes=(
            RawTokenBalanceChange(
                mint=NATIVE_SOL_MINT,
                pre_balance_base_units=lamports if outbound else 0,
                post_balance_base_units=0 if outbound else lamports,
                delta_base_units=delta,
            ),
        ),
    )

def _unsupported_transaction_detail(result: object) -> str:
    supported = (
        "Currently supported: exactly one parsed System Program SOL transfer involving this wallet."
    )
    try:
        transaction_result = _object(result, field="getTransaction.result")
        message = _object(
            _object(transaction_result.get("transaction"), field="transaction").get("message"),
            field="transaction.message",
        )
        instructions = _array(message.get("instructions"), field="message.instructions")
    except PublicSolanaAdapterError:
        return supported
    observed: list[str] = []
    for value in instructions:
        if not isinstance(value, dict):
            continue
        instruction = cast(dict[str, object], value)
        program = instruction.get("program")
        parsed = instruction.get("parsed")
        instruction_type = parsed.get("type") if isinstance(parsed, dict) else None
        if isinstance(program, str) and isinstance(instruction_type, str):
            observed.append(f"{program}:{instruction_type}")
        elif isinstance(program, str):
            observed.append(program)
    if not observed:
        return supported
    return f"Observed instruction types: {', '.join(observed)}. {supported}"

def fetch_public_wallet(
    *,
    public_wallet_address: str,
    endpoint: str = DEFAULT_PUBLIC_SOLANA_ENDPOINT,
    page_limit: int = 100,
    max_pages: int = 2,
    transport: RpcTransport | None = None,
) -> PublicSolanaObservation:
    """Fetch bounded public history without inferring ownership or completeness."""
    address = _validate_address(public_wallet_address)
    endpoint = _validate_endpoint(endpoint)
    if (
        isinstance(page_limit, bool)
        or not isinstance(page_limit, int)
        or not 1 <= page_limit <= 1000
        or isinstance(max_pages, bool)
        or not isinstance(max_pages, int)
        or not 1 <= max_pages <= 100
    ):
        raise PublicSolanaAdapterError("Page limit and max pages are outside safe bounds.")
    send = transport or _http_transport
    signatures: list[dict[str, object]] = []
    response_hashes: list[str] = []
    issues: list[PublicRetrievalIssue] = []
    before: str | None = None
    pages_retrieved = 0
    pagination_complete = False
    stopped_reason = "max_pages_reached"
    request_id = 1

    for _ in range(max_pages):
        config: dict[str, object] = {"commitment": "finalized", "limit": page_limit}
        if before is not None:
            config["before"] = before
        try:
            response = send(
                endpoint,
                _request(
                    method="getSignaturesForAddress",
                    params=[address, config],
                    request_id=request_id,
                ),
            )
        except PublicSolanaRateLimitError as error:
            issues.append(PublicRetrievalIssue("rate_limited", None, str(error)))
            stopped_reason = "rate_limited"
            break
        request_id += 1
        response_hashes.append(_response_hash(response))
        page = _array(
            _rpc_result(response, method="getSignaturesForAddress"),
            field="getSignaturesForAddress.result",
        )
        pages_retrieved += 1
        page_records = [_object(item, field="signature info") for item in page]
        signatures.extend(page_records)
        if len(page_records) < page_limit:
            pagination_complete = True
            stopped_reason = "endpoint_history_exhausted"
            break
        before = _string(page_records[-1].get("signature"), field="signature")

    transactions: list[Transaction] = []
    seen_signatures: set[str] = set()
    transaction_requests = 0
    for item in signatures:
        signature = _string(item.get("signature"), field="signature")
        if signature in seen_signatures:
            issues.append(
                PublicRetrievalIssue(
                    "duplicate_signature", signature, "Signature appeared more than once."
                )
            )
            continue
        seen_signatures.add(signature)
        slot = _integer(item.get("slot"), field="signature.slot")
        try:
            response = send(
                endpoint,
                _request(
                    method="getTransaction",
                    params=[
                        signature,
                        {
                            "commitment": "finalized",
                            "encoding": "jsonParsed",
                            "maxSupportedTransactionVersion": 0,
                        },
                    ],
                    request_id=request_id,
                ),
            )
        except PublicSolanaRateLimitError as error:
            issues.append(PublicRetrievalIssue("rate_limited", signature, str(error)))
            stopped_reason = "rate_limited"
            break
        request_id += 1
        transaction_requests += 1
        response_hashes.append(_response_hash(response))
        result = _rpc_result(response, method="getTransaction")
        if result is None:
            issues.append(
                PublicRetrievalIssue(
                    "transaction_unavailable",
                    signature,
                    "Endpoint returned no confirmed transaction detail.",
                )
            )
            continue
        transaction = _parsed_system_transfer(
            address=address,
            signature=signature,
            signature_slot=slot,
            signature_error=item.get("err"),
            result=result,
        )
        if transaction is None:
            issues.append(
                PublicRetrievalIssue(
                    "unsupported_transaction",
                    signature,
                    _unsupported_transaction_detail(result),
                )
            )
            continue
        transactions.append(transaction)

    return PublicSolanaObservation(
        provenance=PublicSolanaProvenance(
            endpoint=endpoint,
            public_wallet_address=address,
            ownership_status="unverified_public_observation",
            commitment="finalized",
            page_limit=page_limit,
            max_pages=max_pages,
            pages_retrieved=pages_retrieved,
            transaction_requests=transaction_requests,
            newest_signature=(
                _string(signatures[0].get("signature"), field="signature")
                if signatures
                else None
            ),
            oldest_signature=(
                _string(signatures[-1].get("signature"), field="signature")
                if signatures
                else None
            ),
            pagination_complete=pagination_complete,
            history_complete=False,
            stopped_reason=stopped_reason,
            response_sha256=tuple(response_hashes),
        ),
        tokens=(TokenMetadata(mint=NATIVE_SOL_MINT, symbol="SOL", decimals=9),),
        transactions=tuple(
            sorted(
                transactions,
                key=lambda item: (
                    item.timestamp_utc,
                    item.slot,
                    item.source.signature,
                    item.instruction_index,
                ),
            )
        ),
        issues=tuple(issues),
    )

def _transaction_record(transaction: Transaction) -> dict[str, object]:
    record: dict[str, object] = {
        "fixture_record_id": transaction.source.fixture_record_id,
        "source_id": transaction.source.source_id,
        "signature": transaction.source.signature,
        "slot": transaction.slot,
        "timestamp_utc": transaction.timestamp_utc.isoformat().replace("+00:00", "Z"),
        "status": transaction.status.value,
        "instruction_index": transaction.instruction_index,
        "fee_base_units": transaction.fee.fee_base_units,
        "fee_payer": transaction.fee.payer_wallet_id,
        "fee_charged": transaction.fee.charged,
        "instructions": [asdict(item) for item in transaction.instructions],
        "token_balance_changes": [
            asdict(item) for item in transaction.token_balance_changes
        ],
    }
    if transaction.fee.charged:
        record["fee_mint"] = transaction.fee.mint
    return record

def write_public_observation(
    *, observation: PublicSolanaObservation, output_path: Path
) -> Path:
    """Write replayable canonical raw records plus bounded retrieval provenance."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "contract_version": PUBLIC_OBSERVATION_CONTRACT_VERSION,
        "observation_type": "public_solana_wallet",
        "ownership_status": observation.provenance.ownership_status,
        "provenance": asdict(observation.provenance),
        "tokens": [asdict(item) for item in observation.tokens],
        "wallet_id": observation.provenance.public_wallet_address,
        "records": [_transaction_record(item) for item in observation.transactions],
        "issues": [asdict(item) for item in observation.issues],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output_path
