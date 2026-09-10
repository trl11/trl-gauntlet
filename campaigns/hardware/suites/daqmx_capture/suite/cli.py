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
    # sampled suite takes, so only these two are added here.
    parser.add_argument("--driver", choices=["real", "mock"], default=None)
    parser.add_argument("--labels", default=None, help="name the inputs, as `ai0=Rail 3V3, ai2=Ground`")


def _extra_overrides(args: argparse.Namespace) -> dict[str, object]:
    return {"driver": args.driver, "labels": args.labels}


main = make_suite_cli(
    SPEC,
    prog="daqmx-capture",
    description="Capture the configured NI-DAQmx analog inputs for the length of the run.",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)


if __name__ == "__main__":
    sys.exit(main())
