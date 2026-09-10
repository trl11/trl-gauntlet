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
    parser.add_argument("--capture-rate-hz", type=float, default=None, help="capture the waveform at this rate")
    parser.add_argument("--capture-samples", type=int, default=None, help="samples per channel in each capture")


def _extra_overrides(args: argparse.Namespace) -> dict[str, object]:
    return {
        "capture_rate_hz": args.capture_rate_hz,
        "capture_samples": args.capture_samples,
        "driver": args.driver,
        "labels": args.labels,
    }


main = make_suite_cli(
    SPEC,
    prog="daqmx-capture",
    description="Capture the configured NI-DAQmx analog inputs for the length of the run.",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)


if __name__ == "__main__":
    sys.exit(main())
