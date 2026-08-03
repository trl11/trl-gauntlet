"""Command-line entry point."""

from __future__ import annotations

import sys

from gauntlet_suite import make_suite_cli

from suite.runner import SPEC

main = make_suite_cli(SPEC, prog="hardware-trigger", description="Drive a GPIO trigger line over SSH.")

if __name__ == "__main__":
    sys.exit(main())
