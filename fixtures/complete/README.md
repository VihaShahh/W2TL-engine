# Complete Synthetic Fixture

This fixture is the frozen 96-record core history for the two explicitly owned synthetic
wallets. It contains no imported chain data, real wallet address, plausible private key, real
client fact, or network-derived price. Every record is mapped once in `scenario-catalog.json`.

## Record and Output Summary

| Evidence or output | Count |
|---|---:|
| Stored wallet records | 96 |
| Wallet Alpha records | 64 |
| Wallet Beta records | 32 |
| Logical events after evidence processing | 85 |
| Complete disposal rows | 35 |
| Complete income rows | 7 |
| Deliberate exceptions | 24 |

Coverage is 82 resolved records, 7 secondary views, 4 quarantined records, and 3
review-flagged spam receipts. The ground truth freezes classifications, lots, consumptions,
movements, ending inventory, report rows, exceptions, and reconciliation counts.

## Hand-Calculated Spine

1. **FIFO and holding boundary.** Alpha buys 4 SKITO for USD 8 on 2024-01-01. Selling
   3 SKITO for USD 15 at exactly 2025-01-01 consumes USD 6 basis, creates USD 9 gain, and is
   short-term because the policy requires more than one year. One second later, the remaining
   1 SKITO from that lot is long-term.
2. **Acquisition fee.** The next 10-SKITO buy has USD 21 cash basis and a 0.01 SOL fee valued
   at USD 100 per SOL. The fee is USD 1, so the SKITO lot opens at USD 22 basis. The SOL units
   are also consumed as their own disposal; the USD 1 is not applied twice.
3. **Multi-lot movement.** The first 5-SKITO Alpha-to-Beta transfer is stored in both wallet
   histories but becomes one logical event. FIFO source slices become Beta lots with the same
   original acquisition timestamps, basis states, and parent lot IDs.
4. **Known plus unknown disposal.** Beta first owns 1 known-basis MYST for USD 3, then receives
   unknown-basis MYST externally and through an owned transfer. Its 2.5-MYST sale for USD 12.50
   allocates USD 5 per whole unit: the known unit reports USD 5 proceeds, USD 3 basis, and USD 2
   gain; the unknown slices remain linked exceptions rather than zero-basis rows.
5. **Valued swap.** Each complete SKITO-to-NOVA swap gives 1 SKITO and receives 2 NOVA. Exact
   prices of USD 4 per SKITO and USD 2 per NOVA agree on USD 4. The SKITO consumption receives
   USD 4 proceeds and the NOVA lot receives USD 4 basis.
6. **Blocked swap.** One-sided, conflicting, and absent exact prices preserve received NOVA
   quantities with exception-backed unknown basis and create no fabricated dollar result.
7. **Income and spam.** A 1-RWD reward at USD 2 creates USD 2 income and a USD 2 lot. A 2-DROP
   airdrop at USD 0.50 creates USD 1 income and basis. Unpriced SPAM receipts create no income
   and remain quarantined.
8. **Failed fee.** Failed swap legs do not consume SKITO or create NOVA. A proven 0.01 SOL fee
   still consumes SOL and is valued at USD 1; an uncharged failed event mutates no inventory.
9. **Wrap continuity.** The supported 2-SKITO to 2-wSKITO wrap carries the selected source basis,
   original date, and parent lineage without income or disposal. The 1-to-0.5 ambiguous wrap
   suspends the outgoing unit, quarantines the receipt with unknown basis, and asks for evidence.
10. **Unknown outbound.** External SKITO outflows move units to suspense. They are not silently
    treated as sales and cannot be consumed again.

## Minefields

The catalog also names deterministic timestamp ties, an identical duplicate, a contradictory
duplicate pair, instruction/balance mismatch, missing token metadata, insufficient inventory,
third-token fees, unknown-basis movement, the exact holding boundary, and partial-output
lineage. These cases are deliberate and their exceptions are expected run output.

## Maintenance

`scripts/generate_complete_fixture.py` rebuilds only the source fixture and refuses to replace
an existing frozen ground-truth directory. `scripts/freeze_complete_ground_truth.py` is a
review aid, never a test dependency. Ground truth changes require recalculating the examples
above, reviewing stable lineage IDs, and accepting the changed files explicitly.
