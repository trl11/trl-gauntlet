"""Command-line entry point for Tid Pic18f26k83.

``make_suite_cli`` supplies every flag the contract names, plus
``--print-profile-schema`` so Gauntlet can render a profile form.
"""

from __future__ import annotations

import argparse
import sys

from gauntlet_sdk import err, make_suite_cli

from suite.console import ConsoleError
from suite.runner import SPEC


def _extra_args(parser: argparse.ArgumentParser) -> None:
    # --duration-s and --sample-period-s come from make_suite_cli, which every
    # sampled suite takes, so only the driver is added here. The manifest
    # declares it as an override, so Gauntlet forwards it as a flag and this
    # has to accept one.
    parser.add_argument("--driver", choices=["real", "mock"], default=None)


def _extra_overrides(args: argparse.Namespace) -> dict[str, object]:
    return {"driver": args.driver}


_run = make_suite_cli(
    SPEC,
    prog="tid_pic18f26k83",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)


def main(argv: list[str] | None = None) -> int:
    """Run the suite, reporting an unreachable bench rather than raising.

    A console that cannot be found or does not answer is the bench not being
    wired, which is worth one line saying which ports were tried. A traceback
    says the same thing at twenty times the length.
    """
    try:
        return _run(argv)
    except ConsoleError as exc:
        err(str(exc))
        return 2


if __name__ == "__main__":
    sys.exit(main())
