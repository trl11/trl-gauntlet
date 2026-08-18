"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC


def _extra_args(parser: argparse.ArgumentParser) -> None:
    """Flags beyond the contract's own, matching `overrides:` in suite.yaml."""
    parser.add_argument("--driver", choices=["real", "mock"], default=None, help="drive a real unit, or synthesise")


def _extra_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Turn those flags into profile overrides."""
    return {"driver": args.driver}


main = make_suite_cli(
    SPEC,
    prog="ethernet",
    description="Measure Ethernet throughput to and from the unit.",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)

if __name__ == "__main__":
    sys.exit(main())
