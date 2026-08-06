"""Command-line entry point for __SUITE_TITLE__."""

from __future__ import annotations

import sys

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC

main = make_suite_cli(SPEC, prog="__SUITE_KEY__")


if __name__ == "__main__":
    sys.exit(main())
