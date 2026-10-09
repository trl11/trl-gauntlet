"""Command-line entry point for Tid Temperature Sensor."""

from __future__ import annotations

import argparse
import sys
from functools import partial

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC


def _extra_args(parser: argparse.ArgumentParser) -> None:
    # The manifest declares these as overrides, so Gauntlet forwards them as
    # flags and this has to accept them.
    parser.add_argument("--driver", choices=["real", "mock"], default=None)
    parser.add_argument("--part", choices=["tmp100", "tmp112"], default=None)
    parser.add_argument("--address", type=partial(int, base=16), default=None)


def _extra_overrides(args: argparse.Namespace) -> dict[str, object]:
    return {"address": args.address, "driver": args.driver, "part": args.part}


main = make_suite_cli(
    SPEC,
    prog="tid_temperature_sensor",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)


if __name__ == "__main__":
    sys.exit(main())
