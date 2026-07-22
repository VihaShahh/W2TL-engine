# Final Verification

Verified on 2026-07-23 from two fresh output directories using the repository virtual
environment.

## End-to-end result

```bash
.venv/bin/wallet-tax-engine run \
  --fixtures fixtures/complete \
  --output <fresh-output> \
  --policy-id us_federal_assessment_fifo \
  --policy-version 1
```

Both runs wrote 35 disposal rows, 7 income rows, and 24 deliberate exceptions. Reconciliation
passed, and a recursive comparison found no byte difference between the two directories.

| Output | SHA-256 |
|---|---|
| `disposals.csv` | `2789690520b26362d980c5565c0884fedba974e94866cb3ac9b6bab3098e49fd` |
| `income.csv` | `5dfc68ebb402acf1d8c2ac0483a36e85266d35342228553d2bd40078da74ff2a` |
| `exceptions.json` | `2cd0ac028f02b875f45382a33fc49d34c8143ddf4f2bf958e56ea69d2a7a1ac9` |

The separate allocation comparison also completed:

| Output | SHA-256 |
|---|---|
| `comparison.json` | `08c7010fce941fb7df6858594dafcb3f4262d26011a13d759fff9f4268905672` |

## Quality checks

```text
.venv/bin/pytest -q
114 passed

.venv/bin/ruff check src tests scripts
All checks passed

.venv/bin/mypy src tests scripts
Success: no issues found in 65 source files

openspec validate build-wallet-tax-lots-engine --strict
1 passed, 0 failed

openspec validate --specs --strict
1 passed, 0 failed
```

The Python suite deliberately excludes frontend and browser tests. The local viewer was also
smoke-checked by starting the real server: its report page, Solana page, JavaScript asset,
fixture-metadata endpoint, and Python-backed report endpoint all responded successfully.

## Final scope

- **Shipped:** complete deterministic FIFO core, three required outputs, independent ground
  truth, isolated HIFO and specific-identification comparison, local read-only frontend, and a
  bounded public Solana observation adapter.
- **Cut after exploration:** frontend and browser tests, stablecoin aggregation, treasury
  ownership mapping, and broker reconciliation.
- **Not claimed:** arbitrary real-wallet ingestion, production tax treatment, reviewer workflow,
  filing, private credentials, database persistence, or non-U.S. reporting.
