# Architecture

## The idea behind the structure

The design uses a stable shared engine with focused extensions for local rules and replaceable
behavior. Country reporting can change without copying ingestion or lot accounting, and a lot
allocation method can change without copying mutation and conservation code.

There are two independent extension axes:

- a country adapter decides how neutral accounting facts become jurisdiction-shaped reports;
- a lot-allocation strategy decides which eligible lot is consumed next.

`country_US` therefore owns U.S.-shaped disposal rows, while `TaxLotLedger` owns inventory
mutation and conservation. `FifoLedger`, `HifoLedger`, and specific identification share those
mutations and change only selection. A future `country_IN` should not subclass U.S. reporting,
and a future average-cost method should not copy price or exception rules.

This is a narrow extension design, not a plugin framework. I added a seam only where the code
has a second concrete use or where a country boundary would otherwise leak into the core.

## Runtime flow

```text
JSON fixture and policy version
        |
        v
loader -> normalized immutable evidence
        |
        v
duplicate handling -> owned-transfer matching -> classification
        |                                      |
        | unsupported or incomplete            v
        +-------------------------------> exception collector
                                               |
                                               v
exact price resolution -> WalletTaxEngine -> TaxLotLedger
                                               |
                                               v
country_US handler -> reconciliation -> deterministic writers
```

The pipeline is intentionally one-way. Raw evidence is not edited to fit a classification, and
the report layer cannot reach back into inventory to repair an inconvenient result.

## Responsibilities

| Area | Owns | Does not own |
|---|---|---|
| `models` | Immutable evidence, accounting, review, and report contracts | Workflow or storage |
| `evidence` | Validation, normalization, duplicate resolution, transfer matching, classification, source coverage | Tax lot mutation |
| `policy` | Exact policy selection and price resolution | Network price discovery |
| `accounting` | Lots, consumption, movement, fees, swaps, income, wraps, reconciliation | Country report fields |
| `country_US` | U.S.-shaped term and report adaptation | Evidence ingestion |
| `reporting` | Neutral handler contract, method comparison, stable CSV and JSON writers | Estimation |
| `integrations` | Bounded public Solana observation and provenance | Ownership or complete tax history |
| `interfaces` | CLI, local HTTP routes, and static asset serving | Domain decisions |
| `frontend` | Read-only display of Python results | Classification or accounting |

`pipeline.py` is the composition root. It is allowed to know the stages and dispatch supported
classification results. The lower modules should not know the CLI or output directory.

## Invariants that matter

The most important behavior lives below the allocation strategy:

- inventory is keyed by wallet and mint;
- token quantity uses integer base units;
- known basis uses exact `Decimal` values;
- partial splits preserve total quantity and basis by exact subtraction;
- an owned-wallet movement preserves acquisition date, basis, and ancestry;
- unknown basis and unknown acquisition remain states, never numeric defaults;
- every mutation has stable source, event, and lot identifiers;
- reportable and unresolved slices reconcile to the parent event;
- a successful run covers all source records and passes whole-run reconciliation.

That placement is important. HIFO must not get a looser conservation rule than FIFO, and another
country must not get a different definition of known evidence merely because its report differs.

## Exceptions are a product output

An exception is not a log message. It contains stable identity, observed facts, the reason the
engine stopped, affected outputs, source references, related lot or event lineage, and a concrete
request for the missing evidence. That contract allows the future reviewer workflow to treat an
exception as work without inventing a temporary number.

File-level contract errors stop the run. A bad record that can be isolated is quarantined and
the rest of the fixture continues. Accounting continues past a domain exception only when the
remaining mutation can still be defended and reconciled.

## Persistence is intentionally open

The current engine is a deterministic in-memory Python program, which is the simplest fit for a
small offline assessment. Persistence should be chosen with the future host application:

- ORM models would suit evidence bundles, ownership periods, reviewer exceptions, approvals,
  and audit history;
- plain Python should continue to own arithmetic, classification contracts, and lot invariants;
- raw SQL may suit bulk staging, reconciliation queries, or a measured hot path after profiling.

The rule is more important than the technology: ORM defaults or SQL expressions must not turn
missing tax evidence into zero, and database layout must not become the only place a tax position
is encoded.

## Deliberate boundary

The shipped frontend is a local read-only presentation boundary. Its report page invokes the
Python pipeline and its Solana page makes a bounded public observation. Browser code does not
classify events or calculate money. There is still no database or reviewer workflow. A real
source adapter can be added before normalization without rewriting the ledger, and workflow can
be added around evidence and exceptions without moving tax math into presentation code.
