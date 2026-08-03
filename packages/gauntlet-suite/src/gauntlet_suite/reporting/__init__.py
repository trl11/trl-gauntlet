"""Artifact writers for the Gauntlet contract.

Sinks are callables the iteration loop fans outcomes into; the writers are
one-shot functions for the end-of-run artifacts. Everything here works
standalone, so a suite that does not use :func:`gauntlet_suite.run_suite` can
still produce a conforming run directory.
"""

from __future__ import annotations

from gauntlet_suite.reporting.events_sink import EventsSink
from gauntlet_suite.reporting.jsonl_sink import JsonlSink, json_safe
from gauntlet_suite.reporting.junit_sink import JUnitSink
from gauntlet_suite.reporting.manifest import GitState, Manifest, build_manifest, git_state, write_manifest
from gauntlet_suite.reporting.summary import write_summary
from gauntlet_suite.reporting.verdict import make_result, make_test, write_simple_verdict, write_verdict

__all__ = [
    "EventsSink",
    "GitState",
    "JUnitSink",
    "JsonlSink",
    "Manifest",
    "build_manifest",
    "git_state",
    "json_safe",
    "make_result",
    "make_test",
    "write_manifest",
    "write_simple_verdict",
    "write_summary",
    "write_verdict",
]
