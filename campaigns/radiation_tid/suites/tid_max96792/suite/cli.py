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
    # sampled suite takes, so only the rest are added here.
    parser.add_argument("--driver", choices=["real", "mock"], default=None)
    parser.add_argument("--max-width", type=int, default=None)
    parser.add_argument("--part-address", default=None)
    parser.add_argument("--max-errors-per-sample", type=int, default=None)
    parser.add_argument("--snapshot-every", type=int, default=None)
    parser.add_argument("--burst-frames", type=int, default=None)


def _extra_overrides(args: argparse.Namespace) -> dict[str, object]:
    return {
        "burst_frames": args.burst_frames,
        "driver": args.driver,
        "max_errors_per_sample": args.max_errors_per_sample,
        "max_width": args.max_width,
        "part_address": args.part_address,
        "snapshot_every": args.snapshot_every,
    }


main = make_suite_cli(
    SPEC,
    prog="tid-max96792",
    description="Watch the GMSL link while the MAX96792AGTM/VY+ is under the beam.",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)


if __name__ == "__main__":
    sys.exit(main())
