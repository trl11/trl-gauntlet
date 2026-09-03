"""Command-line entry point for Tid Ads7138.

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
    # sampled suite takes, so only the driver is added here. The manifest
    # declares it as an override, so Gauntlet forwards it as a flag and this
    # has to accept one.
    parser.add_argument("--driver", choices=["real", "mock"], default=None)


def _extra_overrides(args: argparse.Namespace) -> dict[str, object]:
    return {"driver": args.driver}


main = make_suite_cli(
    SPEC,
    prog="tid_ads7138",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)


if __name__ == "__main__":
    sys.exit(main())
