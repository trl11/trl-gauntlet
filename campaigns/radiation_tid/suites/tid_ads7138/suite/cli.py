"""Command-line entry point for Tid Ads7138."""

from __future__ import annotations

import sys

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC


main = make_suite_cli(SPEC, prog="tid_ads7138")


if __name__ == "__main__":
    sys.exit(main())
