"""Command-line entry point for Tid Tmp100."""

from __future__ import annotations

import argparse
import sys

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC


def _extra_args(parser: argparse.ArgumentParser) -> None:
    # The manifest declares the driver as an override, so Gauntlet forwards it
    # as a flag and this has to accept one.
    parser.add_argument("--driver", choices=["real", "mock"], default=None)


def _extra_overrides(args: argparse.Namespace) -> dict[str, object]:
    return {"driver": args.driver}


main = make_suite_cli(
    SPEC,
    prog="tid_tmp100",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)


if __name__ == "__main__":
    sys.exit(main())
