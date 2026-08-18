"""Shared fixtures for the LAN7430 dose suite's tests.

The suite package lives beside these tests rather than being installed, so the
suite directory goes on ``sys.path`` the same way Gauntlet puts it there for a
real run.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from gauntlet_sdk import AnomalyLog
from gauntlet_sdk.reporting.jsonl_sink import JsonlSink

SUITE_ROOT = Path(__file__).resolve().parents[1]
if str(SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(SUITE_ROOT))

from suite import mock  # noqa: E402
from suite.profile import TidLan7430Profile  # noqa: E402
from suite.telemetry import TelemetryState  # noqa: E402


class RecordingSink(JsonlSink):
    """A real sink that also keeps its anomalies where a test can read them."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def write_anomaly(self, probe: str, kind: str, detail: Any = None) -> None:
        """Write the record, and keep it."""
        super().write_anomaly(probe, kind, detail)
        self.records.append((probe, kind, dict(detail or {})))

    def kinds(self) -> set[str]:
        """Every ``probe/kind`` pair recorded so far."""
        return {f"{probe}/{kind}" for probe, kind, _ in self.records}


@pytest.fixture
def sink(tmp_path: Path) -> RecordingSink:
    return RecordingSink(tmp_path / "metrics.jsonl")


@pytest.fixture
def anomalies(sink: RecordingSink) -> AnomalyLog:
    return AnomalyLog(sink)


@pytest.fixture
def profile() -> TidLan7430Profile:
    return TidLan7430Profile()


@pytest.fixture
def baseline_sample() -> dict[str, Any]:
    """A healthy part, as the collector would report it."""
    return mock.sample(0, "eth1")


@pytest.fixture
def state(baseline_sample: dict[str, Any]) -> TelemetryState:
    """Telemetry state already baselined against a healthy part."""
    from suite.telemetry import establish_baseline

    fresh = TelemetryState()
    establish_baseline(baseline_sample, fresh)
    return fresh
