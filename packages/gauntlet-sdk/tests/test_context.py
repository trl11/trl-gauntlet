"""The per-run context handed to every iteration."""

from __future__ import annotations

import time

import pytest

from gauntlet_sdk import EventsSink, JsonlSink, JUnitSink, RunEnvironment, SuiteContext


@pytest.fixture
def ctx(tmp_path):
    run_dir = tmp_path / "run"
    jsonl = JsonlSink(run_dir / "metrics.jsonl")
    events = EventsSink(run_dir / "events.sqlite")
    context = SuiteContext(
        suite_name="demo",
        env=RunEnvironment(run_dir=run_dir, run_id="20260101T000000Z-abcd", target="unit-3"),
        profile=None,
        jsonl=jsonl,
        events=events,
        junit=JUnitSink(run_dir / "junit.xml", "demo"),
        sample_period_s=1.0,
    )
    yield context
    jsonl.close()
    events.close()


class TestSuiteContext:
    def test_the_run_directory_and_id_come_from_the_environment(self, ctx, tmp_path):
        assert ctx.run_dir == tmp_path / "run"
        assert ctx.run_id == "20260101T000000Z-abcd"

    def test_the_target_comes_from_the_environment(self, ctx):
        assert ctx.target == "unit-3"

    def test_elapsed_run_time_advances(self, ctx):
        ctx.started_at_monotonic = time.monotonic() - 5.0

        assert ctx.elapsed_run_s >= 5.0

    def test_artifact_resolves_a_path_inside_the_run_directory(self, ctx):
        path = ctx.artifact("frames", "shot.png")

        assert path == ctx.run_dir / "frames" / "shot.png"

    def test_artifact_creates_the_parent_directory(self, ctx):
        path = ctx.artifact("frames", "nested", "shot.png")

        assert path.parent.is_dir()
        assert not path.exists()

    def test_extras_carry_suite_state_between_iterations(self, ctx):
        ctx.extras["handle"] = object()

        assert "handle" in ctx.extras
