"""Run the installed CLI through ``python -m engine``."""

from engine.interfaces.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
