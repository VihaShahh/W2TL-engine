# Edge Cases I Planted

The fixtures are part of the design. A happy-path sample proves that JSON can be parsed; these
cases test whether the engine can refuse a plausible but indefensible answer.

## Ownership and transfers

| Case | Failure to avoid | Implemented treatment |
|---|---|---|
| Transfer between the client's wallets | Sale followed by a zero-basis acquisition | Match both observations and move the original lot slices, basis, and dates |
| External inbound transfer | Treat missing basis as zero | Create exception-backed unknown-basis inventory |
| External outbound with unknown purpose | Assume sale, gift, payment, or self-transfer | Move units to suspense and request ownership and purpose evidence |
| Incomplete internal-transfer evidence | Match on signature alone | Leave the movement unresolved and keep the observations visible |

## Ordering, duplicates, and classification

| Case | Failure to avoid | Implemented treatment |
|---|---|---|
| Equivalent duplicate | Double-count inventory or income | Deduplicate with explicit source coverage |
| Conflicting duplicate | Pick the first record | Quarantine the group |
| Timestamp tie | Let input file order change FIFO | Use documented deterministic chain-shaped tie breakers |
| Unknown instruction | Guess the closest category | Emit an unrecognized-shape exception |
| Failed event with charged fee | Apply reverted legs or lose the real fee | Ignore failed legs and process only separately proven fee evidence |

## Lots and reports

| Case | Failure to avoid | Implemented treatment |
|---|---|---|
| Sale spans several lots | Merge dates and basis into one row | Record and report each consumed lot slice |
| Partial lot | Round away remaining basis | Calculate exactly and derive the remainder by subtraction |
| Sale exceeds inventory | Create a negative lot | Reject the mutation and emit an inventory exception |
| Known and unknown basis mix | Invent a total gain or hide the valid part | Report complete slices and except unresolved slices with parent reconciliation |
| Holding-period boundary | Off-by-one term | Test either side of the selected more-than-one-year rule |

## Prices and token behavior

| Case | Failure to avoid | Implemented treatment |
|---|---|---|
| Exact price missing | Use nearest price or zero | Block dependent money and request price evidence |
| Price sources conflict | Pick one silently | Preserve both observations and emit a conflict |
| Swap legs disagree | Choose a preferred leg | Consume given quantity, preserve received quantity with unknown basis, block money |
| Binary float price | Preserve an unintended approximation | Reject floats; accept decimal strings or integers |
| Worthless spam airdrop | Create fabricated income | Quarantine unpriced spam with no income row |
| Third-token fee | Ignore a property disposal or count it twice | Create a linked fee disposal and apply one explicit context adjustment |
| Ambiguous wrap ratio | Assume tax-free continuity | Preserve unresolved units and emit an exception |

## What counts as covered

Putting a case in JSON is not enough. A case is considered covered when it has a documented
expected result, a focused test, source or lot lineage in its output, and inclusion in the
complete ground truth when it affects the final reports.
