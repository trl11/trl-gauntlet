"""Command-line entry point for Tid Tmp100."""

from __future__ import annotations

import sys

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC

main = make_suite_cli(SPEC, prog="tid_tmp100")


if __name__ == "__main__":
    sys.exit(main())
