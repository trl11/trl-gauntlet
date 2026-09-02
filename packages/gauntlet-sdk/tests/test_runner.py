"""End-to-end behaviour of the iteration loop and run_suite."""

from __future__ import annotations

import json
import signal
import sqlite3
import threading
import time

import pytest
from pydantic import BaseModel, ConfigDict

from gauntlet_sdk import (
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

    def test_without_a_bound_the_loop_runs_until_it_is_stopped(self):
        runner = IterationRunner(_ok, period_s=0)

        def _stop_after_three(ctx):
            if ctx.iteration >= 3:
                runner.request_stop()
            return IterationOutcome(success=True)

        runner._iterate = _stop_after_three
        result, _ = runner.run()

        assert result.total_iterations == 3
        assert result.stopped_early
        assert not result.aborted
        assert result.passed

    def test_duration_mode_stops_at_the_deadline(self):
        result, _ = IterationRunner(_ok, max_duration_s=0.15, period_s=0.05).run()

        assert result.total_iterations >= 1
        assert result.duration_s < 5.0
        assert result.passed

    def test_the_period_paces_successive_iterations(self):
        started = time.monotonic()
        IterationRunner(_ok, max_iterations=3, period_s=0.05).run()

        assert time.monotonic() - started >= 0.1

    def test_abort_ends_the_loop_and_records_the_reason(self):
        runner = IterationRunner(_ok, max_iterations=100, period_s=0)

        def _abort_after_one(_ctx):
            runner.abort("instrument disconnected")
            return IterationOutcome(success=True)

        runner._iterate = _abort_after_one
        result, _ = runner.run()

        assert result.total_iterations == 1
        assert result.aborted
        assert result.abort_reason == "instrument disconnected"
        assert not result.passed

    def test_keyboard_interrupt_inside_an_iteration_aborts_the_run(self):
        def _interrupt(_ctx):
            raise KeyboardInterrupt

        result, outcomes = IterationRunner(_interrupt, max_iterations=5, period_s=0).run()

        assert outcomes == []
        assert result.aborted
        assert result.abort_reason == "keyboard_interrupt"

    def test_an_interrupt_signal_stops_the_loop(self):
        def _raise_sigint(ctx):
            if ctx.iteration == 1:
                signal.raise_signal(signal.SIGINT)
            return IterationOutcome(success=True)

        result, _ = IterationRunner(_raise_sigint, max_iterations=10, period_s=0).run()

        assert result.total_iterations == 1
        assert result.abort_reason == "keyboard_interrupt"

    def test_start_and_stop_callbacks_bracket_the_loop(self):
        seen = []
        runner = IterationRunner(
            _ok,
            max_iterations=1,
            period_s=0,
            on_start=lambda: seen.append("start"),
            on_stop=lambda: seen.append("stop"),
        )
        runner.run()

        assert seen == ["start", "stop"]

    def test_a_failing_stop_callback_does_not_break_the_run(self):
        def _boom():
            raise RuntimeError("could not park the stage")

        result, _ = IterationRunner(_ok, max_iterations=1, period_s=0, on_stop=_boom).run()

        assert result.passed

    def test_a_failing_end_sink_does_not_break_the_run(self):
        runner = IterationRunner(_ok, max_iterations=1, period_s=0)

        def _boom(_result):
            raise RuntimeError("sink closed")

        runner.add_end_sink(_boom)

        assert runner.run()[0].passed

    def test_pass_criteria_that_hold_leave_the_result_alone(self):
        runner = IterationRunner(_ok, max_iterations=2, period_s=0)
        runner.set_pass_criteria(lambda _r, _o: (True, ""))
        result, _ = runner.run()

        assert result.passed
        assert result.failures == 0

    def test_pass_criteria_are_not_consulted_for_an_aborted_run(self):
        runner = IterationRunner(_ok, max_iterations=100, period_s=0)
        runner.set_pass_criteria(lambda _r, _o: (False, "never reached"))

        def _abort(_ctx):
            runner.abort("cable pulled")
            return IterationOutcome(success=True)

        runner._iterate = _abort
        result, _ = runner.run()

        assert result.abort_reason == "cable pulled"

    def test_failing_criteria_after_a_graceful_stop_are_a_failure_not_an_abort(self):
        runner = IterationRunner(_ok, max_iterations=100, period_s=0)
        runner.set_pass_criteria(lambda _r, _o: (False, "too few samples"))

        def _stop_after_one(_ctx):
            runner.request_stop()
            return IterationOutcome(success=True)

        runner._iterate = _stop_after_one
        result, _ = runner.run()

        assert not result.aborted
        assert result.stopped_early
        assert result.failures == 1
        assert not result.passed

    def test_the_loop_runs_on_a_worker_thread_where_signals_cannot_be_installed(self):
        captured = {}

        def _run():
            captured["result"] = IterationRunner(_ok, max_iterations=2, period_s=0).run()[0]

        worker = threading.Thread(target=_run)
        worker.start()
        worker.join(timeout=5)

        assert captured["result"].total_iterations == 2


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

    def test_a_duration_bounded_suite_runs_until_its_deadline(self, tmp_path):
        spec = SuiteSpec(
            name="demo",
            profile_model=Profile,
            iterate=_always_ok,
            duration_seconds=lambda _p: 0.1,
            sample_period_seconds=lambda p: p.sample_period_s,
        )
        result, run_dir = run_suite(spec, Profile(), run_dir=tmp_path / "run")

        assert result.total_iterations >= 1
        assert (run_dir / "verdict.json").is_file()

    def test_a_zero_duration_suite_samples_until_it_is_stopped(self, tmp_path):
        def _stop_on_third(_ctx, ictx):
            if ictx.iteration == 3:
                signal.raise_signal(signal.SIGUSR1)
            return IterationOutcome(success=True)

        spec = SuiteSpec(
            name="demo",
            profile_model=Profile,
            iterate=_stop_on_third,
            duration_seconds=lambda _p: 0.0,
            sample_period_seconds=lambda p: p.sample_period_s,
        )
        result, run_dir = run_suite(spec, Profile(), run_dir=tmp_path / "run")

        assert result.total_iterations == 3
        assert result.stopped_early
        verdict = json.loads((run_dir / "verdict.json").read_text())
        assert verdict["passed"] is True
        assert verdict["aborted"] is False

    def test_a_zero_count_suite_cycles_until_it_is_stopped(self, tmp_path):
        def _stop_on_second(_ctx, ictx):
            if ictx.iteration == 2:
                signal.raise_signal(signal.SIGUSR1)
            return IterationOutcome(success=True)

        spec = _spec(_stop_on_second)
        result, _ = run_suite(spec, Profile(iterations=0), run_dir=tmp_path / "run")

        assert result.total_iterations == 2
        assert result.stopped_early
        assert result.passed

    def test_evaluate_can_fail_a_run_whose_iterations_all_passed(self, tmp_path):
        spec = _spec(evaluate=lambda _outcomes, _profile: (False, "throughput below floor"))
        _, run_dir = run_suite(spec, Profile(), run_dir=tmp_path / "run")

        verdict = json.loads((run_dir / "verdict.json").read_text())
        assert verdict["passed"] is False
        assert "throughput below floor" in verdict["reason"]

    def test_evaluate_returning_nothing_keeps_the_default_criterion(self, tmp_path):
        spec = _spec(evaluate=lambda _outcomes, _profile: None)
        result, _ = run_suite(spec, Profile(), run_dir=tmp_path / "run")

        assert result.passed

    def test_the_stop_signal_ends_the_loop_and_still_writes_a_verdict(self, tmp_path):
        def _stop_on_first(_ctx, ictx):
            if ictx.iteration == 1:
                signal.raise_signal(signal.SIGUSR1)
            return IterationOutcome(success=True)

        spec = _spec(_stop_on_first)
        result, run_dir = run_suite(spec, Profile(iterations=50), run_dir=tmp_path / "run")

        assert result.total_iterations == 1
        assert result.stopped_early
        assert json.loads((run_dir / "verdict.json").read_text())["stopped_early"] is True

    def test_a_run_on_a_worker_thread_cannot_install_signal_handlers(self, tmp_path):
        captured = {}

        def _run():
            captured["result"] = run_suite(_spec(), Profile(), run_dir=tmp_path / "run")[0]

        worker = threading.Thread(target=_run)
        worker.start()
        worker.join(timeout=10)

        assert captured["result"].passed

    def test_teardown_is_skipped_when_setup_fails(self, tmp_path):
        seen = []

        def _bad_setup(_ctx):
            raise RuntimeError("instrument missing")

        spec = _spec(setup=_bad_setup, teardown=lambda ctx: seen.append("teardown"))
        with pytest.raises(RuntimeError, match="instrument missing"):
            run_suite(spec, Profile(), run_dir=tmp_path / "run")
        assert seen == []
