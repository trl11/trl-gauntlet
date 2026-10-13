"""Command-line entry point.

``make_suite_cli`` supplies every flag the contract names, ``--duration-s``
among them, plus ``--print-profile-schema`` so Gauntlet can render a profile
form.
"""

from __future__ import annotations

import sys

from gauntlet_sdk import make_suite_cli

from suite.runner import SPEC

main = make_suite_cli(
    SPEC,
    prog="daq-record",
    description="Hold the run open while Gauntlet records the bench's DAQs.",
)


if __name__ == "__main__":
    sys.exit(main())
