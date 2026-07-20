"""Parse commands and pass validated paths to the offline pipeline."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


def _add_policy_arguments(parser: argparse.ArgumentParser) -> None:
    from engine.policy.config import US_FIFO_POLICY

    parser.add_argument(
        "--policy-id",
        default=US_FIFO_POLICY.policy_id,
        help=f"Assessment policy ID (default: {US_FIFO_POLICY.policy_id}).",
    )
    parser.add_argument(
        "--policy-version",
        type=int,
        default=US_FIFO_POLICY.policy_version,
        help=f"Assessment policy version (default: {US_FIFO_POLICY.policy_version}).",
    )

def build_parser() -> argparse.ArgumentParser:
    """Build the CLI without performing fixture or accounting work."""
    parser = argparse.ArgumentParser(
        prog="wallet-tax-engine",
        description="Convert synthetic wallet evidence into traceable tax-lot outputs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="Run the offline fixture pipeline.")
    run_parser.add_argument("--fixtures", type=Path, required=True, help="Fixture directory.")
    run_parser.add_argument("--output", type=Path, required=True, help="Output directory.")
    _add_policy_arguments(run_parser)
    explain_parser = subparsers.add_parser(
        "explain", help="Run the product example with a method-by-method trace."
    )
    explain_parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path("fixtures/complete"),
        help="Fixture directory (default: fixtures/complete).",
    )
    explain_parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/explain"),
        help="Output directory (default: output/explain).",
    )
    _add_policy_arguments(explain_parser)
    comparison_parser = subparsers.add_parser(
        "compare-methods", help="Generate isolated FIFO, HIFO, and specific-ID results."
    )
    comparison_parser.add_argument(
        "--fixture", type=Path, required=True, help="Alternative-allocation fixture file."
    )
    comparison_parser.add_argument(
        "--output", type=Path, required=True, help="Comparison output directory."
    )
    _add_policy_arguments(comparison_parser)
    return parser

def main(argv: Sequence[str] | None = None) -> int:
    """Execute one CLI command and return its process exit code."""
    args = build_parser().parse_args(argv)
    if args.command in {"run", "explain"}:
        from engine.accounting import ReconciliationError
        from engine.evidence.loader import FixtureValidationError
        from engine.pipeline import run_pipeline
        from engine.policy.config import (
            UnsupportedContractError,
            UnsupportedPolicyError,
            select_policy,
        )

        try:
            trace = print if args.command == "explain" else None
            policy = select_policy(
                policy_id=args.policy_id,
                policy_version=args.policy_version,
            )
            result = run_pipeline(
                fixtures_dir=args.fixtures,
                output_dir=args.output,
                policy=policy,
                trace=trace,
            )
        except (
            FixtureValidationError,
            ReconciliationError,
            UnsupportedContractError,
            UnsupportedPolicyError,
        ) as error:
            print(f"Run failed: {error}", file=sys.stderr)
            return 1
        print(
            f"Wrote {result.disposal_count} disposal row(s), "
            f"{result.income_count} income row(s), and "
            f"{result.exception_count} exception(s) to {args.output}."
        )
        print("Reconciliation passed.")
        return 0
    if args.command == "compare-methods":
        from engine.policy.config import select_policy
        from engine.reporting.comparison import write_method_comparison

        policy = select_policy(
            policy_id=args.policy_id,
            policy_version=args.policy_version,
        )
        comparison_path = write_method_comparison(
            fixture_path=args.fixture,
            output_dir=args.output,
            policy=policy,
        )
        print(f"Wrote allocation-method comparison to {comparison_path}.")
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")
