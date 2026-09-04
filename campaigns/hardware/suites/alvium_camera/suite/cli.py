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
    # --sample-period-s comes from make_suite_cli, which every sampled suite
    # takes, so only the rest are added here.
    parser.add_argument("--driver", choices=["real", "mock"], default=None)
    parser.add_argument("--frames", type=int, default=None)
    parser.add_argument("--max-width", type=int, default=None)


def _extra_overrides(args: argparse.Namespace) -> dict[str, object]:
    return {"driver": args.driver, "frames": args.frames, "max_width": args.max_width}


main = make_suite_cli(
    SPEC,
    prog="alvium-camera-check",
    description="Take a few stills from the Allied Vision camera and say whether it is working.",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)


if __name__ == "__main__":
    sys.exit(main())
