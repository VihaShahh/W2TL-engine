# Technical Notes

The root [`README.md`](../README.md) is the current product contract and
[`MY-NOTES.txt`](../MY-NOTES.txt) is the single first-person account of decisions, cuts, AI use,
and next steps. This folder stays deliberately small:

1. [`architecture.md`](architecture.md) explains the boundaries and extension choices.
2. [`data-flow.md`](data-flow.md) follows one fixture run from evidence to reports.
3. [`edge-cases.md`](edge-cases.md) records the failure cases the engine must handle honestly.
4. [`final-verification.md`](final-verification.md) records the commands and last verified result.
5. [`../fixtures/complete/README.md`](../fixtures/complete/README.md) documents the generated
   fixture and its hand-checked accounting spine.

These notes describe code that exists now. Future ideas are labelled as future work and live in
the human notes instead of being spread across several speculative documents. The confidential
assessment remains in `setup.pdf` and must not be distributed outside its authorized channel.
