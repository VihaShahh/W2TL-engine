# Wallet-to-Tax-Lots Engine

This is an offline Python engine that turns two synthetic wallet histories into wallet-scoped
tax lots, Form 8949-shaped disposal rows, ordinary-income rows, and actionable exceptions.

The central rule is simple: a guessed number never leaves the engine. Missing basis, price,
ownership, purpose, or classification evidence remains an explicit state and, when it blocks an
output, becomes a structured exception.

## Install it

Python 3.11 or newer is required. The engine has no third-party runtime dependency and the core
run uses no network, key, account, database, or external price service.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

## Run the engine with the frontend

The recommended demo command starts both parts of the application: the Python backend and the
browser frontend. There is no separate Node, npm, or frontend development server to start.

From the repository root, run:

```bash
.venv/bin/wallet-tax-engine serve \
  --fixtures fixtures/complete \
  --host 127.0.0.1 \
  --port 4173
```

Keep that terminal open, then visit `http://127.0.0.1:4173` in a browser. The server provides
the HTML, CSS, and JavaScript from `frontend/`, exposes the local `/api/demo` and `/api/process`
endpoints, and runs the real Python accounting pipeline when the report is requested. Open
`http://127.0.0.1:4173/solana.html` for the separate bounded public-Solana observation page.
Press `Ctrl+C` in the terminal to stop both the backend and frontend.

The main report is fully offline. The Solana observation page is optional and requires network
access to the configured public RPC endpoint. It does not feed its observations into the
verified synthetic tax report.

## Run only the command-line report

To write the three report files without opening the frontend, run:

```bash
.venv/bin/wallet-tax-engine run \
  --fixtures fixtures/complete \
  --output output/complete \
  --policy-id us_federal_assessment_fifo \
  --policy-version 1
```

That one engine command writes:

| File | Contents |
|---|---|
| `disposals.csv` | Complete disposal slices with dates, proceeds, basis, gain or loss, term, and lineage |
| `income.csv` | Defensibly valued staking reward and airdrop income with created-lot lineage |
| `exceptions.json` | Observed facts, the blocking reason, affected output, and evidence to request |

The complete fixture currently produces 35 disposal rows, 7 income rows, and 24 deliberate
exceptions. Reconciliation must pass before the command reports success. Reviewed copies of the
expected outputs live under `fixtures/complete/ground_truth/`.

For a stage-by-stage trace of the same real pipeline:

```bash
.venv/bin/wallet-tax-engine explain \
  --fixtures fixtures/complete \
  --output output/explain
```

## What is implemented

The fixture has 96 stored records across two wallets owned by the same synthetic client. After
duplicate handling and matching the two views of internal transfers, those records represent 85
logical events. Every source record is resolved, review-flagged, quarantined, or recorded as a
secondary view.

The required path covers:

- buys, sales, swaps, transfers in and out, staking rewards, airdrops, fees, and failed events;
- wallet-and-mint FIFO lots, including partial and multi-lot consumption;
- carried basis and acquisition dates for proven transfers between the two owned wallets;
- short-term and long-term disposal rows;
- exact-timestamp price evidence with deliberate missing and conflicting prices;
- unknown-basis inventory and mixed known/unknown disposals;
- ordinary and spam airdrops, third-token fees, failed transactions, and supported or ambiguous
  wraps;
- deterministic reports, source-to-lot lineage, accounting reconciliation, and independent
  ground truth.

Token amounts stay in integer base units. Prices, basis, proceeds, income, and gains use
`Decimal` values parsed from strings. Binary floats are rejected. Formatting to cents happens
only at the report boundary.

### SKITO and mint

`SKITO` is short for Skibidi Token, an invented token symbol used in the fixtures like a fake
stock ticker in an accounting example. It is not a real coin or wallet asset. A **mint** is the token's unique
on-chain identifier on Solana. The symbol is for humans; the mint tells software exactly which
token a balance belongs to. Every mint in this repository is visibly synthetic.

## The architecture choice

The structure keeps the stable domain core small and places country-specific positions and
replaceable strategies at narrow edges.

```text
fixture evidence
    -> validation and normalization
    -> duplicate and owned-transfer resolution
    -> classification or exception
    -> exact price resolution
    -> TaxLotLedger mutation and reconciliation
    -> country_US report adapter
    -> deterministic CSV and JSON writers
```

`TaxLotLedger` owns inventory mutation and conservation. FIFO, HIFO, and specific
identification vary only lot selection. `country_US` owns the U.S.-shaped report decisions;
evidence, uncertainty, and lot mechanics do not import them. This keeps two independent axes of
extension: another allocation method should not copy accounting logic, and another country
should not inherit U.S. assumptions.

This is not a generic plugin framework. The seams exist because the repository already has a
second concrete behavior. The isolated method comparison demonstrates that boundary:

```bash
.venv/bin/wallet-tax-engine compare-methods \
  --fixture fixtures/bonus/alternative-allocation.json \
  --output output/method-comparison
```

Persistence is left as a future deployment decision. Plain Python is appropriate for the
current deterministic engine. We can use ORM models for evidence, exceptions, ownership,
approvals, and audit history. Raw SQL may be appropriate for bulk
staging, reconciliation, or measured performance bottlenecks. Tax rules and unknown-evidence
handling should remain in the domain layer instead of depending on database defaults.

## Judgment calls

- **Pricing:** only an exact mint, UTC timestamp, currency, and source-backed price match is
  accepted. There is no nearest-price, interpolation, carry-forward, network, or zero fallback.
- **Internal transfers:** compatible ownership, signature, mint, quantity, direction, and time
  evidence must match. The lot moves with its original date, basis, and lineage. No disposal or
  income is created.
- **Unknown inbound basis:** quantity becomes exception-backed inventory. Basis is unknown, not
  zero.
- **Partial results:** complete lot slices may report when complete and unresolved quantities
  reconcile to their parent disposal. The unresolved slice remains linked to its exception.
- **External outbound transfers:** unknown ownership or purpose moves the lot to suspense. It is
  not assumed to be a sale, gift, payment, or self-transfer.
- **Swaps:** both exact leg values must exist and agree. Otherwise received quantity is preserved
  with unknown basis and dependent dollar output is blocked.
- **Income and spam:** a valued reward or ordinary airdrop creates income and equal lot basis.
  Unpriced spam is quarantined without fabricated income.
- **Fees and failed events:** proven third-token fees are separate disposals. Reverted economic
  legs do not mutate inventory, but a separately proven charged fee may still be processed.
- **Wraps:** a documented one-to-one same-owner synthetic wrap carries continuity. An ambiguous
  ratio becomes an exception.
- **Holding term:** under the selected assessment policy, long term means strictly more than one
  year.

These are versioned engineering positions for the assessment, not tax advice or filing logic.

## What I cut

The frontend ships as a read-only local viewer, but frontend and browser tests are deliberately
not included. Stablecoin aggregation, treasury ownership, and broker reconciliation were cut
because they need more evidence and policy depth. The allocation-method comparison stayed
because it directly proves a core architectural seam.

Also out of scope: real client data, private credentials, paid APIs, inferred ownership,
automatic price lookup, arbitrary wallet imports, authentication, multi-client storage,
transaction editing, approvals, tax filing, and non-U.S. implementations.

## Real-data readiness

The engine cannot process arbitrary real transaction histories today. The loader deliberately
requires visibly synthetic IDs, exactly two owned wallets, fixture price evidence, a scenario
catalog, and reviewed ground truth. Real use still needs chain and exchange adapters,
program-aware decoding, ownership evidence, replayable pricing, multi-wallet and multi-chain
identity, incremental imports, reorg handling, reviewer workflow, security controls, scale
tests, and independent tax review.

The accounting core is designed to survive that next step. Its exact arithmetic, explicit
unknown states, deterministic ordering, lot lineage, exception contract, and reconciliation do
not depend on synthetic JSON. A real adapter should feed the same neutral evidence contract and
leave unsupported shapes visible, not bypass the engine's safety rules.

The optional public Solana path is narrower than real ingestion. It retrieves bounded public
history, records pagination and endpoint provenance, and preserves unsupported shapes. It does
not establish ownership, complete history, tax classification, price evidence, or opening basis.

## Verify it

Install the pinned development tools and run:

```bash
.venv/bin/python -m pip install pytest==8.4.2 ruff==0.15.16 mypy==2.1.0
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts
.venv/bin/mypy src tests scripts
```

The tests cover contracts, loading, source coverage, duplicate and transfer resolution,
classification, exact prices, FIFO conservation, swaps, income, spam, fees, failures, wraps,
reports, CLI failures, ground truth, and deterministic output. There are intentionally no
frontend or browser tests; the frontend remains presentation-only and outside the accounting
quality gate.

## Repository map

| Path | Responsibility |
|---|---|
| `src/engine/evidence/` | Fixture loading, normalization, matching, classification, and source coverage |
| `src/engine/policy/` | Versioned assessment policy and exact price resolution |
| `src/engine/accounting/` | Lot ledgers, event accounting, and reconciliation |
| `src/engine/country_US/` | U.S.-specific report adaptation |
| `src/engine/reporting/` | Neutral reporting contracts, comparison, and deterministic writers |
| `src/engine/integrations/` | Optional bounded public Solana observation adapter |
| `src/engine/interfaces/` | CLI and local loopback web server |
| `frontend/` | Dependency-free report and public-observation pages |
| `fixtures/complete/` | The 96-record adversarial fixture and reviewed ground truth |
| `fixtures/bonus/` | The isolated allocation-method comparison input |
| `tests/` | Focused and end-to-end verification |
| `docs/` | Architecture, data flow, edge cases, and verification notes |

Read [`MY-NOTES.txt`](MY-NOTES.txt) for the first-person reasoning, cuts, decision journal,
two-paragraph another-day note, real-data assessment, and unrequested next idea. The technical
reading order starts at [`docs/README.md`](docs/README.md).
