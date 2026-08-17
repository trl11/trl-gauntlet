"""Command-line entry point for the LAN7430 dose suite."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC


def _extra_args(parser: argparse.ArgumentParser) -> None:
    """Flags beyond the contract's own, matching `overrides:` in suite.yaml."""
    parser.add_argument("--driver", choices=["real", "mock"], default=None, help="drive real hardware, or synthesise")
    parser.add_argument("--ssh-user", default=None, help="login on the unit (default trl)")
    parser.add_argument("--ssh-key-path", default=None, help="private key to authenticate with")


def _extra_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Turn those flags into profile overrides."""
    return {"driver": args.driver, "ssh_key_path": args.ssh_key_path, "ssh_user": args.ssh_user}


main = make_suite_cli(
    SPEC,
    prog="tid_lan7430",
    description="Measure LAN7430 throughput and everything about the part that dose can move.",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)

if __name__ == "__main__":
    sys.exit(main())
