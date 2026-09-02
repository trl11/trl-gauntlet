"""Command-line entry point.

``make_suite_cli`` supplies every flag the contract names, plus
``--print-profile-schema`` so Gauntlet can render a profile form. The extra
flags here are the thresholds, and each one also appears in the ``overrides:``
list of ``suite.yaml``.
"""

from __future__ import annotations

import argparse
import sys

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC


def _extra_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-cpu-percent", type=float, default=None)
    parser.add_argument("--max-load-per-core", type=float, default=None)
    parser.add_argument("--max-new-interface-errors", type=int, default=None)
    parser.add_argument("--max-temperature-c", type=float, default=None)
    parser.add_argument("--min-available-memory-percent", type=float, default=None)
    parser.add_argument("--min-free-disk-percent", type=float, default=None)
    parser.add_argument("--stop-on-failure", action="store_true", default=None)


def _extra_overrides(args: argparse.Namespace) -> dict[str, object]:
    return {
        "max_cpu_percent": args.max_cpu_percent,
        "max_load_per_core": args.max_load_per_core,
        "max_new_interface_errors": args.max_new_interface_errors,
        "max_temperature_c": args.max_temperature_c,
        "min_available_memory_percent": args.min_available_memory_percent,
        "min_free_disk_percent": args.min_free_disk_percent,
        "stop_on_failure": args.stop_on_failure,
    }


main = make_suite_cli(
    SPEC,
    prog="system-stats",
    description="Sample Linux system statistics and check each reading against the profile.",
    extra_args=_extra_args,
    extra_overrides=_extra_overrides,
)


if __name__ == "__main__":
    sys.exit(main())
