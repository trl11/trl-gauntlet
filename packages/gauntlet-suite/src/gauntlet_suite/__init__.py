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

Suites with a shape the loop does not fit use :mod:`gauntlet_suite.reporting`
directly and write the artifacts themselves. Both satisfy the contract; see
``docs/contract.md`` for what it requires.
"""

from __future__ import annotations

from gauntlet_suite.anomalies import AnomalyLog
from gauntlet_suite.cli import make_suite_cli
from gauntlet_suite.context import SuiteContext
from gauntlet_suite.counters import CounterTracker, Observation
from gauntlet_suite.environment import Capability, RunEnvironment, new_run_id, run_environment
from gauntlet_suite.iteration import (
    IterationContext,
    IterationOutcome,
    IterationRunner,
    RunResult,
)
from gauntlet_suite.log import err, info, warn
from gauntlet_suite.monitor import RemoteMonitor
from gauntlet_suite.phases import PhaseRecord, PhaseTimer
from gauntlet_suite.profile import ProfileError, load_profile, snapshot_profile, summarize_profile
from gauntlet_suite.remote import RemoteError, RemoteTarget
from gauntlet_suite.reporting import (
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
from gauntlet_suite.runner import SuiteSpec, run_suite

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
