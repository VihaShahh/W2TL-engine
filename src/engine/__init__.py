"""Installable package for the wallet-to-tax-lots engine.

The CLI sends fixture paths to the pipeline, which coordinates typed domain modules.
Tests import the same package so commands and test cases exercise one implementation.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
