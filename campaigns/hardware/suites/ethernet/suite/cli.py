"""Command-line entry point."""

from __future__ import annotations

import sys

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC

main = make_suite_cli(SPEC, prog="ethernet", description="Measure Ethernet throughput to and from the unit.")

if __name__ == "__main__":
    sys.exit(main())
