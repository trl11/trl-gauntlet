"""End-to-end behaviour of the iteration loop and run_suite."""

from __future__ import annotations

import json
import sqlite3

import pytest
from pydantic import BaseModel, ConfigDict

from gauntlet_suite import (
    IterationContext,
    IterationOutcome,
    IterationRunner,
    SuiteSpec,
    run_suite,
)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iterations: int = 3
    sample_period_s: float = 0.0
    fail_at: int = 0


def _always_ok(_ctx, ictx: IterationContext) -> IterationOutcome:
    return IterationOutcome(success=True, metrics={"n": ictx.iteration})


def _fail_at(ctx, ictx: IterationContext) -> IterationOutcome:
    if ictx.iteration == ctx.profile.fail_at:
        return IterationOutcome(success=False, reason=f"failed at {ictx.iteration}")
    return IterationOutcome(success=True, metrics={"n": ictx.iteration})


def _spec(iterate=_always_ok, **kwargs) -> SuiteSpec:
    return SuiteSpec(
        name="demo",
        profile_model=Profile,
        iterate=iterate,
        iteration_count=lambda p: p.iterations,
        sample_period_seconds=lambda p: p.sample_period_s,
        **kwargs,
    )


class TestIterationRunner:
    def test_count_mode_runs_exactly_that_many(self):
        result, outcomes = IterationRunner(_ok, max_iterations=5, period_s=0).run()
        assert result.total_iterations == 5
        assert len(outcomes) == 5
        assert result.passed

    def test_exception_in_iterate_becomes_a_failed_iteration(self):
        def _boom(_ctx):
            raise RuntimeError("sensor exploded")

        result, outcomes = IterationRunner(_boom, max_iterations=2, period_s=0).run()
        assert result.failures == 2
        assert "sensor exploded" in outcomes[0].reason
        assert not result.passed

    def test_stop_on_failure_ends_the_loop(self):
        calls = {"n": 0}

        def _fail_second(_ctx):
            calls["n"] += 1
            return IterationOutcome(success=calls["n"] != 2, reason="bad" if calls["n"] == 2 else "")

        result, _ = IterationRunner(_fail_second, max_iterations=10, period_s=0, stop_on_failure=True).run()
        assert result.total_iterations == 2
        assert result.aborted

    def test_pass_criteria_can_fail_an_otherwise_clean_run(self):
        runner = IterationRunner(_ok, max_iterations=3, period_s=0)
        runner.set_pass_criteria(lambda _r, _o: (False, "aggregate too low"))
        result, _ = runner.run()
        assert not result.passed
        assert "aggregate too low" in result.abort_reason

    def test_graceful_stop_is_not_an_abort(self):
        runner = IterationRunner(_ok, max_iterations=100, period_s=0)

        def _stop_after_two(ctx):
            if ctx.iteration >= 2:
                runner.request_stop()
            return IterationOutcome(success=True)

        runner._iterate = _stop_after_two
        result, _ = runner.run()
        assert result.total_iterations == 2
        assert result.stopped_early
        assert not result.aborted
        assert result.passed

    def test_requires_a_bound(self):
        with pytest.raises(ValueError, match="max_iterations or max_duration_s"):
            IterationRunner(_ok)


def _ok(_ctx) -> IterationOutcome:
    return IterationOutcome(success=True)


class TestSuiteSpec:
    def test_needs_exactly_one_bound(self):
        with pytest.raises(ValueError, match="exactly one"):
            SuiteSpec(
                name="x",
                profile_model=Profile,
                iterate=_always_ok,
                duration_seconds=lambda p: 1.0,
                iteration_count=lambda p: 1,
            )

        with pytest.raises(ValueError, match="duration_seconds or iteration_count"):
            SuiteSpec(name="x", profile_model=Profile, iterate=_always_ok)

    def test_rejects_a_mismatched_profile(self):
        class Other(BaseModel):
            pass

        with pytest.raises(TypeError, match="expected Profile"):
            run_suite(_spec(), Other())


class TestRunSuite:
    def test_writes_every_declared_artifact(self, tmp_path):
        result, run_dir = run_suite(_spec(), Profile(), run_dir=tmp_path / "run")

        assert result.passed
        assert run_dir == tmp_path / "run"
        for name in ("verdict.json", "manifest.json", "metrics.jsonl", "junit.xml", "events.sqlite", "summary.md"):
            assert (run_dir / name).is_file(), f"{name} was not written"

    def test_writes_into_the_directory_it_is_given(self, tmp_path):
        target = tmp_path / "explicit"
        _, run_dir = run_suite(_spec(), Profile(), run_dir=target)
        assert run_dir == target

    def test_verdict_records_the_failure_reason(self, tmp_path):
        _, run_dir = run_suite(_spec(_fail_at), Profile(fail_at=2), run_dir=tmp_path / "run")
        verdict = json.loads((run_dir / "verdict.json").read_text())
        assert verdict["passed"] is False
        assert verdict["failures"] == 1
        assert verdict["reason"]

    def test_metrics_has_one_record_per_iteration(self, tmp_path):
        _, run_dir = run_suite(_spec(), Profile(iterations=4), run_dir=tmp_path / "run")
        records = [json.loads(line) for line in (run_dir / "metrics.jsonl").read_text().splitlines() if line]
        assert [r["iteration"] for r in records] == [1, 2, 3, 4]
        assert all(r["success"] for r in records)

    def test_events_db_mirrors_metrics(self, tmp_path):
        _, run_dir = run_suite(_spec(), Profile(iterations=3), run_dir=tmp_path / "run")
        with sqlite3.connect(run_dir / "events.sqlite") as conn:
            assert conn.execute("SELECT COUNT(*) FROM iterations").fetchone()[0] == 3

    def test_teardown_runs_even_when_iterate_raises(self, tmp_path):
        seen = []

        def _boom(_ctx, _ictx):
            raise RuntimeError("nope")

        spec = _spec(_boom, setup=lambda ctx: seen.append("setup"), teardown=lambda ctx: seen.append("teardown"))
        run_suite(spec, Profile(iterations=1), run_dir=tmp_path / "run")
        assert seen == ["setup", "teardown"]

    def test_teardown_is_skipped_when_setup_fails(self, tmp_path):
        seen = []

        def _bad_setup(_ctx):
            raise RuntimeError("instrument missing")

        spec = _spec(setup=_bad_setup, teardown=lambda ctx: seen.append("teardown"))
        with pytest.raises(RuntimeError, match="instrument missing"):
            run_suite(spec, Profile(), run_dir=tmp_path / "run")
        assert seen == []
