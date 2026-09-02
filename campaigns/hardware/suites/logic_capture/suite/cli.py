"""Command-line entry point.

``make_suite_cli`` supplies every flag the contract names, plus
``--print-profile-schema`` so Gauntlet can render a profile form.
"""

from __future__ import annotations

import argparse
import sys

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC


def _extra_args(parser: argparse.ArgumentParser) -> None:
    # --duration-s and --sample-period-s come from make_suite_cli, which every
    # sampled suite takes, so only what is particular to a capture is added here.
    parser.add_argument("--driver", choices=["real", "mock"], default=None)
    parser.add_argument("--rate", default=None)
    parser.add_argument("--window", default=None)


def _extra_overrides(args: argparse.Namespace) -> dict[str, object]:
    return {"driver": args.driver, "rate": args.rate, "window": args.window}


main = make_suite_cli(
    SPEC,
    prog="logic-capture",
    description="Capture the analyzer's probes for the length of the run.",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)


if __name__ == "__main__":
    sys.exit(main())
