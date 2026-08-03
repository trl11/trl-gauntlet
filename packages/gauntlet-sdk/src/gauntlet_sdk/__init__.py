"""Write test suites that conform to the Gauntlet contract.

Two ways in. Most suites describe themselves with a :class:`SuiteSpec` and let
:func:`run_suite` handle the loop and every required artifact::

    SPEC = SuiteSpec(
        name="thermal_cycle",
        profile_model=ThermalProfile,
        iterate=_iterate,
        duration_seconds=lambda p: p.duration_s,
    )
    main = make_suite_cli(SPEC)

Suites with a shape the loop does not fit use :mod:`gauntlet_sdk.reporting`
directly and write the artifacts themselves. Both satisfy the contract; see
``docs/contract.md`` for what it requires.
"""

from __future__ import annotations

from gauntlet_sdk.anomalies import AnomalyLog
from gauntlet_sdk.cli import make_suite_cli
from gauntlet_sdk.context import SuiteContext
from gauntlet_sdk.counters import CounterTracker, Observation
from gauntlet_sdk.environment import Capability, RunEnvironment, new_run_id, run_environment
from gauntlet_sdk.iteration import (
    IterationContext,
    IterationOutcome,
    IterationRunner,
    RunResult,
)
from gauntlet_sdk.log import err, info, warn
from gauntlet_sdk.monitor import RemoteMonitor
from gauntlet_sdk.phases import PhaseRecord, PhaseTimer
from gauntlet_sdk.profile import ProfileError, load_profile, snapshot_profile, summarize_profile
from gauntlet_sdk.remote import RemoteError, RemoteTarget
from gauntlet_sdk.reporting import (
    EventsSink,
    JsonlSink,
    JUnitSink,
    Manifest,
    build_manifest,
    make_result,
    make_test,
    write_manifest,
    write_simple_verdict,
    write_summary,
    write_verdict,
)
from gauntlet_sdk.runner import SuiteSpec, run_suite

__version__ = "0.1.0"

__all__ = [
    "AnomalyLog",
    "Capability",
    "CounterTracker",
    "EventsSink",
    "IterationContext",
    "IterationOutcome",
    "IterationRunner",
    "JUnitSink",
    "JsonlSink",
    "Manifest",
    "Observation",
    "PhaseRecord",
    "PhaseTimer",
    "ProfileError",
    "RemoteError",
    "RemoteMonitor",
    "RemoteTarget",
    "RunEnvironment",
    "RunResult",
    "SuiteContext",
    "SuiteSpec",
    "__version__",
    "build_manifest",
    "err",
    "info",
    "load_profile",
    "make_result",
    "make_suite_cli",
    "make_test",
    "new_run_id",
    "run_environment",
    "run_suite",
    "snapshot_profile",
    "summarize_profile",
    "warn",
    "write_manifest",
    "write_simple_verdict",
    "write_summary",
    "write_verdict",
]
