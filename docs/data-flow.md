# Data Flow

This follows the complete fixture through the same path used by the `run` command. The concrete
scenario counts and expected accounting are in
[`fixtures/complete/README.md`](../fixtures/complete/README.md).

`SKITO` is short for Skibidi Token, the made-up token symbol in these examples. A mint is the
token's unique Solana identity; symbols are display labels and are not reliable unique keys. All
fixture mints are synthetic.

## 1. Select policy and load evidence

The command selects one exact policy ID and version. There is no silent fallback to a newer or
similar policy. The loader then reads the manifest, two wallet histories, ownership, token
metadata, exact-timestamp USD prices, and scenario catalog.

It validates synthetic identities, UTC timestamps, integer base units, decimal strings, balance
arithmetic, and references between files. A structural file error stops the run. A malformed
record that can be isolated is retained as a source-linked exception so unrelated evidence can
continue.

For example, if pre-balance plus delta does not equal post-balance, the loader does not choose
which field looks plausible. It quarantines the record and reports all three observed values.

## 2. Normalize without assigning tax meaning

The normalizer converts source records into immutable events with stable IDs, ordering facts,
wallet and counterparty IDs, token legs, fee evidence, and cash evidence. It does not assign a
gain, lot, tax form, or inferred price.

Stable ordering uses observed timestamp and chain-shaped tie breakers rather than JSON file
order. This matters because two valid FIFO lots can share a timestamp.

## 3. Resolve repeated observations

Duplicate handling distinguishes three cases:

- equivalent records from the same wallet become one event plus documented duplicate coverage;
- conflicting records from the same wallet are quarantined;
- compatible records from different owned wallets remain available for transfer matching.

An internal transfer needs compatible ownership, signature, mint, quantity, direction, and time
facts. Its incoming record is a second observation of the same event, not a second acquisition.

## 4. Classify or except every source record

Classification follows explicit precedence and records the rule and supporting source IDs. A
coverage ledger closes every stored record as resolved, review-flagged, quarantined, or a
secondary view. Unknown program shapes do not fall through to a familiar category.

Classifications include buy, sell, swap, internal transfer, transfer in, transfer out, reward,
airdrop, fee, failed transaction, and supported wrap behavior. If facts conflict, the output is
an exception rather than a best guess.

## 5. Resolve only exact prices

When an event needs a market value, the price resolver matches mint, UTC timestamp, currency,
and price source. One matching observation produces a sourced `Decimal`. No observation means
missing; competing observations mean conflict. Neither state is converted to zero or a nearby
price.

## 6. Mutate wallet-scoped lots

The selected ledger consumes eligible lots within one wallet and mint. FIFO is required for the
complete run. The base ledger performs the mutation, records consumption or movement, and checks
quantity and known-basis conservation.

A simple internal-transfer example is:

```text
Wallet Alpha before transfer
  1 SKITO from Lot A, basis USD 2
  3 SKITO from Lot B, basis USD 9

Wallet Beta after transfer
  child of Lot A, 1 SKITO, basis USD 2, original acquisition date
  child of Lot B, 3 SKITO, basis USD 9, original acquisition date

Tax result at transfer
  no proceeds, gain, loss, or income
```

If a later sale consumes known and unknown basis, complete slices can report and the unresolved
slice becomes an exception. Their quantities and allocated proceeds must still add back to the
parent sale.

External outbound units with unknown purpose move to suspense. They leave spendable inventory
without being called a sale. Failed transactions do not apply reverted token legs. Supported
fees, swaps, income, and wraps use the same ledger and exception contracts.

## 7. Adapt and write reports

The U.S. handler receives neutral consumption facts and produces one row for each complete lot
slice. Different acquisition dates or basis values remain separate rows. It does not mutate the
ledger.

The writer sorts stable identities, formats money to cents, uses fixed line endings, and writes:

```text
disposals.csv
income.csv
exceptions.json
```

## 8. Reconcile before success

The final reconciliation checks inventory, consumptions, movements, report coverage, exception
links, and source coverage. Writers are deterministic, but determinism alone is not correctness;
the tests also compare the run with separately reviewed classifications, lots, reports, and
exceptions under `fixtures/complete/ground_truth/`.

Use `explain` to see these stages with real IDs from the fixture:

```bash
.venv/bin/wallet-tax-engine explain \
  --fixtures fixtures/complete \
  --output output/explain
```
