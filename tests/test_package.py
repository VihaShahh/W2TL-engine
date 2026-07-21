"""Smoke checks for the installed package and CLI boundary."""

from importlib.util import find_spec

import pytest

from engine import __version__
from engine.interfaces.cli import main


def test_package_exposes_version() -> None:
    assert __version__ == "0.1.0"

def test_legacy_package_name_is_not_exposed() -> None:
    assert find_spec("wallet_tax_engine") is None

def test_cli_help_is_available(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])

    assert error.value.code == 0
    assert "wallet-tax-engine" in capsys.readouterr().out
