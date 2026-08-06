"""Command-line entry point for Tid Ap7387q."""

from __future__ import annotations

import sys

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC

main = make_suite_cli(SPEC, prog="tid_ap7387q")


if __name__ == "__main__":
    sys.exit(main())
