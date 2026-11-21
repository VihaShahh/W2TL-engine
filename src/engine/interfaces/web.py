"""Serve a local demo workbench backed by the real wallet tax pipeline."""

from __future__ import annotations

import csv
import json
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from sysconfig import get_path
from tempfile import TemporaryDirectory
from typing import ClassVar, cast
from urllib.parse import parse_qs, urlsplit

from engine.evidence.loader import load_fixture
from engine.integrations import (
    DEFAULT_PUBLIC_SOLANA_ENDPOINT,
    PublicSolanaAdapterError,
    PublicSolanaRateLimitError,
    fetch_public_wallet,
)
from engine.pipeline import run_pipeline
from engine.policy.config import CONTRACT_VERSION, US_FIFO_POLICY

SOURCE_FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
INSTALLED_FRONTEND_DIR = (
    Path(get_path("data")) / "share" / "wallet-tax-engine" / "frontend"
)
FRONTEND_DIR = (
    SOURCE_FRONTEND_DIR
    if SOURCE_FRONTEND_DIR.is_dir()
    else INSTALLED_FRONTEND_DIR
)
STATIC_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/solana.html": ("solana.html", "text/html; charset=utf-8"),
    "/solana.js": ("solana.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
DEMO_SCENARIOS = {
    "core": Path("fixtures/complete"),
    "complete": Path("fixtures/complete"),
}

def _friendly_wallet_name(wallet_id: str, *, index: int) -> str:
    aliases = ("Primary Wallet", "Trading Wallet")
    return aliases[index] if index < len(aliases) else f"Demo Wallet {index + 1}"

def _iso_date_range(timestamps: list[str]) -> dict[str, str | None]:
    if not timestamps:
        return {"first": None, "last": None}
    return {"first": min(timestamps), "last": max(timestamps)}

def build_demo_payload(fixtures_dir: Path, *, scenario_id: str = "custom") -> dict[str, object]:
    """Build friendly wallet metadata from validated synthetic fixture evidence."""
    fixture = load_fixture(fixtures_dir, policy=US_FIFO_POLICY)
    token_by_mint = {token.mint: token.symbol for token in fixture.tokens}
    wallets: list[dict[str, object]] = []

    for index, wallet_id in enumerate(fixture.owned_wallet_ids):
        transactions = [
            item for item in fixture.transactions if item.source.wallet_id == wallet_id
        ]
        source_record_count = sum(
            source.wallet_id == wallet_id for source in fixture.all_source_refs
        )
        activity_counts = Counter(
            instruction.instruction_type.replace("_", " ").title()
            for transaction in transactions
            for instruction in transaction.instructions[:1]
        )
        symbols = sorted(
            {
                token_by_mint[instruction.mint]
                for transaction in transactions
                for instruction in transaction.instructions
                if instruction.mint in token_by_mint
            }
        )
        activity_range = _iso_date_range(
            [transaction.timestamp_utc.date().isoformat() for transaction in transactions]
        )
        wallets.append(
            {
                "name": _friendly_wallet_name(wallet_id, index=index),
                "walletId": wallet_id,
                "recordCount": source_record_count,
                "activityRange": activity_range,
                "assets": symbols,
                "activityCounts": dict(sorted(activity_counts.items())),
            }
        )

    return {
        "fixture": {
            "scenarioId": scenario_id,
            "fixtureSetId": fixture.fixture_set_id,
            "contractVersion": CONTRACT_VERSION,
            "clientId": fixture.client_id,
            "recordCount": len(fixture.all_source_refs),
            "quarantinedAtLoad": len(fixture.load_exceptions),
            "policyId": US_FIFO_POLICY.policy_id,
            "basisMethod": US_FIFO_POLICY.basis_method.value,
            "valuationCurrency": US_FIFO_POLICY.valuation_currency.value,
        },
        "wallets": wallets,
    }

def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as source:
        return [dict(row) for row in csv.DictReader(source)]

def _read_json_object(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"Expected a JSON object in {path}.")
    return cast(dict[str, object], value)

def _money_total(rows: list[dict[str, str]], field: str) -> str:
    total = sum((Decimal(row[field]) for row in rows), start=Decimal(0))
    return format(total, ".2f")

def _form_section(rows: list[dict[str, str]], *, term: str) -> dict[str, object]:
    selected = [row for row in rows if row["term"] == term]
    presentation_rows = [
        {
            "description": f'{row["quantity"]} {row["symbol"]}',
            "dateAcquired": row["date_acquired"],
            "dateDisposed": row["date_disposed"],
            "proceedsUsd": row["proceeds_usd"],
            "costBasisUsd": row["cost_basis_usd"],
            "adjustmentCode": None,
            "adjustmentAmountUsd": None,
            "gainLossUsd": row["gain_loss_usd"],
            "walletId": row["wallet_id"],
            "disposalId": row["disposal_id"],
            "eventId": row["event_id"],
            "sourceId": row["source_id"],
            "lotId": row["lot_id"],
        }
        for row in selected
    ]
    return {
        "term": term,
        "rows": presentation_rows,
        "totals": {
            "proceedsUsd": _money_total(selected, "proceeds_usd"),
            "costBasisUsd": _money_total(selected, "cost_basis_usd"),
            "adjustmentAmountUsd": None,
            "gainLossUsd": _money_total(selected, "gain_loss_usd"),
        },
    }

def _income_section(rows: list[dict[str, str]]) -> dict[str, object]:
    return {
        "rows": [
            {
                "description": f'{row["quantity"]} {row["symbol"]}',
                "receivedAt": row["received_at"],
                "priceUsd": row["price_usd"],
                "fairValueUsd": row["fair_value_usd"],
                "category": row["category"],
                "walletId": row["wallet_id"],
                "incomeId": row["income_id"],
                "eventId": row["event_id"],
                "sourceId": row["source_id"],
                "createdLotId": row["created_lot_id"],
            }
            for row in rows
        ],
        "totals": {"fairValueUsd": _money_total(rows, "fair_value_usd")},
    }

def build_processed_payload(
    fixtures_dir: Path, *, scenario_id: str = "custom", row_limit: int | None = None
) -> dict[str, object]:
    """Run the engine and adapt only its generated exports for browser presentation."""
    with TemporaryDirectory(prefix="w2tl-demo-") as temporary_dir:
        output_dir = Path(temporary_dir)
        result = run_pipeline(
            fixtures_dir=fixtures_dir,
            output_dir=output_dir,
            policy=US_FIFO_POLICY,
        )
        disposal_rows = _read_csv_rows(output_dir / "disposals.csv")
        income_rows = _read_csv_rows(output_dir / "income.csv")
        exception_payload = _read_json_object(output_dir / "exceptions.json")

    exceptions = exception_payload.get("exceptions")
    if not isinstance(exceptions, list):
        raise ValueError("Generated exception output has no exceptions list.")
    tax_years = sorted({row["date_disposed"][:4] for row in disposal_rows})
    tax_year = tax_years[0] if len(tax_years) == 1 else "Multiple"
    max_report_rows = len(disposal_rows) + len(income_rows)
    display_limit = (
        max_report_rows
        if row_limit is None
        else max(1, min(row_limit, max_report_rows))
    )
    selected_disposals = disposal_rows[:display_limit]
    selected_income = income_rows[: max(0, display_limit - len(selected_disposals))]

    return {
        "run": {
            "scenarioId": scenario_id,
            "fixtureSetId": exception_payload["fixture_set_id"],
            "contractVersion": exception_payload["contract_version"],
            "policyId": exception_payload["policy_id"],
            "policyVersion": exception_payload["policy_version"],
            "valuationCurrency": exception_payload["valuation_currency"],
            "basisMethod": US_FIFO_POLICY.basis_method.value,
            "basisScope": US_FIFO_POLICY.basis_scope.value,
            "taxYear": tax_year,
            "disposalCount": result.disposal_count,
            "incomeCount": result.income_count,
            "exceptionCount": result.exception_count,
            "displayedReportRows": len(selected_disposals) + len(selected_income),
            "maxReportRows": max_report_rows,
        },
        "report": {
            "form": "8949",
            "title": "Sales and Other Dispositions of Capital Assets",
            "shortTerm": _form_section(selected_disposals, term="short"),
            "longTerm": _form_section(selected_disposals, term="long"),
            "income": _income_section(selected_income),
        },
        "exceptions": exceptions,
    }

def build_public_solana_payload(
    *,
    wallet_address: str,
    endpoint: str = DEFAULT_PUBLIC_SOLANA_ENDPOINT,
    page_limit: int = 5,
    max_pages: int = 1,
) -> dict[str, object]:
    """Fetch a bounded public Solana observation and expose review-safe fields."""
    observation = fetch_public_wallet(
        public_wallet_address=wallet_address,
        endpoint=endpoint,
        page_limit=page_limit,
        max_pages=max_pages,
    )
    return {
        "provenance": asdict(observation.provenance),
        "tokens": [asdict(item) for item in observation.tokens],
        "records": [
            {
                "sourceId": transaction.source.source_id,
                "signature": transaction.source.signature,
                "slot": transaction.slot,
                "timestampUtc": transaction.timestamp_utc.isoformat().replace("+00:00", "Z"),
                "status": transaction.status.value,
                "instructionIndex": transaction.instruction_index,
                "feeBaseUnits": transaction.fee.fee_base_units,
                "feePayer": transaction.fee.payer_wallet_id,
                "instructionCount": len(transaction.instructions),
                "instructions": [
                    {
                        "program": instruction.program,
                        "instructionType": instruction.instruction_type,
                        "mint": instruction.mint,
                        "quantityBaseUnits": instruction.quantity_base_units,
                        "cashValueUsd": (
                            None
                            if instruction.cash_value_usd is None
                            else format(instruction.cash_value_usd, "f")
                        ),
                        "counterpartyWalletId": instruction.counterparty_wallet_id,
                    }
                    for instruction in transaction.instructions
                ],
                "balanceChanges": [asdict(item) for item in transaction.token_balance_changes],
            }
            for transaction in observation.transactions
        ],
        "issues": [asdict(item) for item in observation.issues],
    }

def _json_int(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("Expected an integer JSON value.")
    return int(value)

def _scenario_path(scenario_id: str, *, fallback: Path) -> tuple[str, Path]:
    if not scenario_id:
        return "custom", fallback
    if scenario_id not in DEMO_SCENARIOS:
        raise ValueError("Unknown demo scenario.")
    return scenario_id, DEMO_SCENARIOS[scenario_id]

class DemoRequestHandler(BaseHTTPRequestHandler):
    """Serve only the packaged workbench and its two local JSON operations."""

    fixtures_dir: ClassVar[Path] = Path("fixtures/complete")
    frontend_dir: ClassVar[Path] = FRONTEND_DIR
    server_version = "W2TLDemo/1.0"

    def _send_bytes(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = (json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        self._send_bytes(status, "application/json; charset=utf-8", body)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": message})

    def _read_json_body(self) -> dict[str, object]:
        content_length = int(self.headers.get("Content-Length", "0"))
        if content_length > 4096:
            raise ValueError("Request body is too large.")
        payload = json.loads(self.rfile.read(content_length).decode("utf-8") or "{}")
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object.")
        return cast(dict[str, object], payload)

    def do_GET(self) -> None:
        request_url = urlsplit(self.path)
        path = request_url.path
        if path == "/api/demo":
            try:
                query = parse_qs(request_url.query)
                scenario_id, fixtures_dir = _scenario_path(
                    query.get("scenario", [""])[0], fallback=self.fixtures_dir
                )
                self._send_json(
                    HTTPStatus.OK,
                    build_demo_payload(fixtures_dir, scenario_id=scenario_id),
                )
            except (OSError, ValueError) as error:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))
            return

        asset = STATIC_ASSETS.get(path)
        if asset is None:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Not found.")
            return
        filename, content_type = asset
        try:
            body = (self.frontend_dir / filename).read_bytes()
        except OSError:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "Asset unavailable.")
            return
        self._send_bytes(HTTPStatus.OK, content_type, body)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/process":
            try:
                payload = self._read_json_body()
                scenario_id, fixtures_dir = _scenario_path(
                    str(payload.get("scenario", "")), fallback=self.fixtures_dir
                )
                self._send_json(
                    HTTPStatus.OK,
                    build_processed_payload(
                        fixtures_dir,
                        scenario_id=scenario_id,
                        row_limit=_json_int(payload.get("rowLimit"), default=20),
                    ),
                )
            except (OSError, ValueError) as error:
                self._send_error_json(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
            return
        if path == "/api/public-solana":
            try:
                payload = self._read_json_body()
                self._send_json(
                    HTTPStatus.OK,
                    build_public_solana_payload(
                        wallet_address=str(payload.get("walletAddress", "")),
                        endpoint=str(
                            payload.get("endpoint", DEFAULT_PUBLIC_SOLANA_ENDPOINT)
                        ),
                        page_limit=_json_int(payload.get("pageLimit"), default=5),
                        max_pages=_json_int(payload.get("maxPages"), default=1),
                    ),
                )
            except (
                json.JSONDecodeError,
                OSError,
                PublicSolanaAdapterError,
                PublicSolanaRateLimitError,
                TypeError,
                ValueError,
            ) as error:
                self._send_error_json(HTTPStatus.UNPROCESSABLE_ENTITY, str(error))
            return
        else:
            self._send_error_json(HTTPStatus.NOT_FOUND, "Not found.")

def _handler_for(
    fixtures_dir: Path, *, frontend_dir: Path
) -> type[DemoRequestHandler]:
    class ConfiguredDemoRequestHandler(DemoRequestHandler):
        pass

    ConfiguredDemoRequestHandler.fixtures_dir = fixtures_dir.resolve()
    ConfiguredDemoRequestHandler.frontend_dir = frontend_dir.resolve()
    return ConfiguredDemoRequestHandler

def create_server(
    *,
    fixtures_dir: Path,
    host: str,
    port: int,
    frontend_dir: Path = FRONTEND_DIR,
) -> ThreadingHTTPServer:
    """Create a configured HTTP server without entering its blocking loop."""
    return ThreadingHTTPServer(
        (host, port),
        _handler_for(fixtures_dir, frontend_dir=frontend_dir),
    )

def serve(*, fixtures_dir: Path, host: str, port: int) -> None:
    """Serve the local workbench until interrupted."""
    server = create_server(fixtures_dir=fixtures_dir, host=host, port=port)
    bound_host, bound_port = cast(tuple[str, int], server.server_address)
    print(f"W2TL demo available at http://{bound_host}:{bound_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
