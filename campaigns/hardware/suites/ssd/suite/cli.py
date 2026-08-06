"""Command-line entry point."""

from __future__ import annotations

import sys

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC

main = make_suite_cli(SPEC, prog="ssd", description="Probe SSD bandwidth, integrity and SMART counters.")

if __name__ == "__main__":
    sys.exit(main())
