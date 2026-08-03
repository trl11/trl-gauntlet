"""Command-line entry point.

``make_suite_cli`` supplies every flag the contract names, plus
``--print-profile-schema`` so Gauntlet can render a profile form.
"""

from __future__ import annotations

import argparse
import sys

from gauntlet_suite import make_suite_cli

from suite.runner import SPEC


def _extra_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stop-on-failure", action="store_true", default=None)
    parser.add_argument("--max-temperature-c", type=float, default=None)


def _extra_overrides(args: argparse.Namespace) -> dict[str, object]:
    return {
        "max_temperature_c": args.max_temperature_c,
        "stop_on_failure": args.stop_on_failure,
    }


main = make_suite_cli(
    SPEC,
    prog="example-sampled",
    description="Sample a value on a fixed cadence and check it against a limit.",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)


if __name__ == "__main__":
    sys.exit(main())
