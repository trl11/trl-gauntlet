"""Command-line entry point for Tid Ads7138 Pair.

``make_suite_cli`` supplies every flag the contract names, plus
``--print-profile-schema`` so Gauntlet can render a profile form.
"""

from __future__ import annotations

import argparse
import sys

from gauntlet_sdk import make_suite_cli

from suite.profile import PATTERN_SETS
from suite.runner import SPEC


def _extra_args(parser: argparse.ArgumentParser) -> None:
    # --duration-s and --sample-period-s come from make_suite_cli, which every
    # sampled suite takes, so only these two are added here. The manifest
    # declares both as overrides, so Gauntlet forwards them as flags and this
    # has to accept them.
    parser.add_argument("--driver", choices=["real", "mock"], default=None)
    parser.add_argument("--patterns", choices=sorted(PATTERN_SETS), default=None)


def _extra_overrides(args: argparse.Namespace) -> dict[str, object]:
    return {"driver": args.driver, "patterns": args.patterns}


main = make_suite_cli(
    SPEC,
    prog="tid_ads7138_pair",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)


if __name__ == "__main__":
    sys.exit(main())
