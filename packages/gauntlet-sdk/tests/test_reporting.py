"""The artifact writers: metrics, events, junit, manifest, verdict, summary."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import pytest

from gauntlet_sdk import (
    EventsSink,
    IterationContext,
    IterationOutcome,
    JsonlSink,
    JUnitSink,
    PhaseRecord,
    RunResult,
    build_manifest,
    make_result,
    make_test,
    write_manifest,
    write_simple_verdict,
    write_summary,
    write_verdict,
)
from gauntlet_sdk.reporting.jsonl_sink import json_safe
from gauntlet_sdk.reporting.manifest import git_state


def _ctx(iteration: int = 1) -> IterationContext:
    return IterationContext(iteration=iteration, start_time=0.0, elapsed_run_s=float(iteration), deadline=None)


def _result(**kwargs) -> RunResult:
    defaults = {
        "total_iterations": 2,
        "successes": 2,
        "failures": 0,
        "started_at": 1_767_225_600.0,
        "ended_at": 1_767_225_612.5,
        "aborted": False,
    }
    return RunResult(**{**defaults, **kwargs})


class TestJsonlSink:
    def _records(self, sink: JsonlSink) -> list[dict]:
        return [json.loads(line) for line in sink.path.read_text().splitlines() if line]

    def test_the_parent_directory_is_created(self, tmp_path):
        sink = JsonlSink(tmp_path / "nested" / "deeper" / "metrics.jsonl")
        sink.close()

        assert (tmp_path / "nested" / "deeper" / "metrics.jsonl").is_file()

    def test_an_iteration_record_carries_the_outcome_and_its_phases(self, tmp_path):
        sink = JsonlSink(tmp_path / "metrics.jsonl")
        outcome = IterationOutcome(
            success=False,
            reason="too hot",
            metrics={"temperature_c": 91.5},
            phase_records=[PhaseRecord(name="measure", elapsed_s=0.25, success=False, error="ValueError: x")],
        )
        sink(outcome, _ctx(3))
        sink.close()

        record = self._records(sink)[0]
        assert record["kind"] == "iteration"
        assert record["iteration"] == 3
        assert record["success"] is False
        assert record["reason"] == "too hot"
        assert record["metrics"] == {"temperature_c": 91.5}
        assert record["phases"][0]["name"] == "measure"
        assert record["phases"][0]["error"] == "ValueError: x"

    def test_live_records_carry_an_elapsed_time_when_given(self, tmp_path):
        sink = JsonlSink(tmp_path / "metrics.jsonl")
        sink.write_live({"uut": {"load_1m": 0.5}}, elapsed_run_s=12.0)
        sink.close()

        record = self._records(sink)[0]
        assert record["kind"] == "live"
        assert record["elapsed_run_s"] == 12.0
        assert record["metrics"] == {"uut": {"load_1m": 0.5}}

    def test_live_elapsed_is_null_when_not_given(self, tmp_path):
        sink = JsonlSink(tmp_path / "metrics.jsonl")
        sink.write_live({"load_1m": 0.5})
        sink.close()

        assert self._records(sink)[0]["elapsed_run_s"] is None

    def test_an_anomaly_without_detail_records_an_empty_mapping(self, tmp_path):
        sink = JsonlSink(tmp_path / "metrics.jsonl")
        sink.write_anomaly("link", "frame_lost")
        sink.close()

        assert self._records(sink)[0]["detail"] == {}

    def test_writing_after_close_is_dropped_rather_than_raising(self, tmp_path):
        sink = JsonlSink(tmp_path / "metrics.jsonl")
        sink.close()
        sink.write_live({"v": 1})

        assert self._records(sink) == []

    def test_close_is_idempotent(self, tmp_path):
        sink = JsonlSink(tmp_path / "metrics.jsonl")
        sink.close()
        sink.close()


class TestJsonSafe:
    def test_scalars_pass_through(self):
        assert json_safe(1) == 1
        assert json_safe(True) is True
        assert json_safe(None) is None
        assert json_safe("x") == "x"

    def test_a_dataclass_becomes_a_mapping(self):
        @dataclass
        class Reading:
            volts: float

        assert json_safe(Reading(volts=3.3)) == {"volts": 3.3}

    def test_dictionary_keys_are_stringified(self):
        assert json_safe({1: "a"}) == {"1": "a"}

    def test_tuples_become_lists(self):
        assert json_safe((1, 2)) == [1, 2]

    def test_anything_else_falls_back_to_repr(self):
        assert json_safe({1, 2}) in ("{1, 2}", "{2, 1}")

    def test_nesting_is_coerced_all_the_way_down(self):
        assert json_safe({"a": [{"b": (1,)}]}) == {"a": [{"b": [1]}]}


class TestEventsSink:
    def test_an_iteration_and_its_phases_are_queryable(self, tmp_path):
        sink = EventsSink(tmp_path / "events.sqlite")
        outcome = IterationOutcome(
            success=True,
            metrics={"v": 1},
            phase_records=[PhaseRecord(name="boot", elapsed_s=0.5, success=True)],
        )
        sink(outcome, _ctx(1))
        sink.close()

        with sqlite3.connect(tmp_path / "events.sqlite") as conn:
            assert conn.execute("SELECT success FROM iterations").fetchone()[0] == 1
            assert conn.execute("SELECT name FROM phases").fetchone()[0] == "boot"

    def test_writing_after_close_is_dropped_rather_than_raising(self, tmp_path):
        sink = EventsSink(tmp_path / "events.sqlite")
        sink.close()
        sink(IterationOutcome(success=True), _ctx(1))

        with sqlite3.connect(tmp_path / "events.sqlite") as conn:
            assert conn.execute("SELECT COUNT(*) FROM iterations").fetchone()[0] == 0

    def test_close_is_idempotent(self, tmp_path):
        sink = EventsSink(tmp_path / "events.sqlite")
        sink.close()
        sink.close()


class TestJUnitSink:
    def _write(self, tmp_path, outcomes) -> ET.Element:
        sink = JUnitSink(tmp_path / "junit.xml", "demo")
        for index, outcome in enumerate(outcomes, start=1):
            sink(outcome, _ctx(index))
        sink.bind()(_result(total_iterations=len(outcomes), failures=sum(1 for o in outcomes if not o.success)))
        return ET.parse(tmp_path / "junit.xml").getroot()

    def test_the_suite_element_counts_every_iteration(self, tmp_path):
        root = self._write(tmp_path, [IterationOutcome(success=True), IterationOutcome(success=True)])

        assert root.tag == "testsuite"
        assert root.attrib["name"] == "demo"
        assert root.attrib["tests"] == "2"
        assert root.attrib["failures"] == "0"
        assert root.attrib["errors"] == "0"

    def test_a_passing_iteration_has_no_failure_element(self, tmp_path):
        root = self._write(tmp_path, [IterationOutcome(success=True)])

        case = root.find("testcase")
        assert case.attrib["name"] == "iteration-1"
        assert case.find("failure") is None

    def test_a_failure_carries_the_reason_as_its_message(self, tmp_path):
        root = self._write(tmp_path, [IterationOutcome(success=False, reason="too hot")])

        assert root.find("testcase/failure").attrib["message"] == "too hot"

    def test_a_failure_with_no_reason_still_has_a_message(self, tmp_path):
        root = self._write(tmp_path, [IterationOutcome(success=False)])

        assert root.find("testcase/failure").attrib["message"] == "iteration failed"

    def test_the_failure_body_lists_each_phase_and_its_error(self, tmp_path):
        outcome = IterationOutcome(
            success=False,
            reason="boot never finished",
            phase_records=[
                PhaseRecord(name="connect", elapsed_s=0.5, success=True),
                PhaseRecord(name="boot", elapsed_s=60.0, success=False, error="TimeoutError: no response"),
            ],
        )
        root = self._write(tmp_path, [outcome])

        body = root.find("testcase/failure").text
        assert "reason: boot never finished" in body
        assert "connect: 0.50s [ok]" in body
        assert "boot: 60.00s [FAIL] - TimeoutError: no response" in body

    def test_case_time_is_the_sum_of_its_phases(self, tmp_path):
        outcome = IterationOutcome(
            success=True,
            phase_records=[
                PhaseRecord(name="a", elapsed_s=0.25, success=True),
                PhaseRecord(name="b", elapsed_s=0.75, success=True),
            ],
        )
        root = self._write(tmp_path, [outcome])

        assert root.find("testcase").attrib["time"] == "1.000"


class TestManifest:
    def test_the_manifest_round_trips_through_disk(self, tmp_path):
        manifest = build_manifest(suite="demo", run_id="r1", profile_summary={"iterations": "2"})
        write_manifest(tmp_path / "nested" / "manifest.json", manifest)

        payload = json.loads((tmp_path / "nested" / "manifest.json").read_text())
        assert payload["suite"] == "demo"
        assert payload["run_id"] == "r1"
        assert payload["profile_summary"] == {"iterations": "2"}
        assert payload["command_line"]

    def test_the_started_timestamp_can_be_supplied(self):
        manifest = build_manifest(suite="demo", run_id="r1", started_at_utc="2026-01-01T00:00:00Z")

        assert manifest.started_at_utc == "2026-01-01T00:00:00Z"

    def test_gauntlet_environment_variables_are_captured(self, monkeypatch):
        monkeypatch.setenv("GAUNTLET_TARGET", "unit-3")
        monkeypatch.setenv("UNRELATED", "ignored")

        manifest = build_manifest(suite="demo", run_id="r1")

        assert manifest.env["GAUNTLET_TARGET"] == "unit-3"
        assert "UNRELATED" not in manifest.env

    def test_extra_env_is_merged_over_what_was_captured(self, monkeypatch):
        monkeypatch.setenv("GAUNTLET_TARGET", "unit-3")

        manifest = build_manifest(suite="demo", run_id="r1", extra_env={"GAUNTLET_TARGET": "unit-9"})

        assert manifest.env["GAUNTLET_TARGET"] == "unit-9"

    def test_the_profile_path_is_recorded_as_a_string(self, tmp_path):
        manifest = build_manifest(suite="demo", run_id="r1", profile_path=tmp_path / "quick.yaml")

        assert manifest.profile_path == str(tmp_path / "quick.yaml")

    def test_git_state_outside_a_repository_is_empty(self, tmp_path):
        state = git_state(tmp_path)

        assert state.sha is None
        assert state.branch is None
        assert state.dirty is False

    def test_git_state_is_empty_when_git_is_not_installed(self, monkeypatch):
        def _no_git(*_args, **_kwargs):
            raise FileNotFoundError("git")

        monkeypatch.setattr("gauntlet_sdk.reporting.manifest.subprocess.run", _no_git)

        assert git_state().sha is None

    def test_git_state_describes_a_real_repository(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
        (tmp_path / "tracked.txt").write_text("one\n")
        subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "first"], cwd=tmp_path, check=True)

        state = git_state(tmp_path)

        assert len(state.sha) == 40
        assert state.branch
        assert state.dirty is False

    def test_an_uncommitted_change_makes_the_tree_dirty(self, tmp_path):
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
        (tmp_path / "tracked.txt").write_text("one\n")
        subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-qm", "first"], cwd=tmp_path, check=True)
        (tmp_path / "tracked.txt").write_text("two\n")

        assert git_state(tmp_path).dirty is True


class TestVerdict:
    def test_make_result_omits_what_it_was_not_given(self):
        assert make_result("throughput", "Throughput", 91.2) == {
            "key": "throughput",
            "label": "Throughput",
            "value": 91.2,
            "format": "text",
        }

    def test_make_result_carries_unit_precision_and_highlight(self):
        entry = make_result("t", "T", 1, format="bytes", unit="MB/s", precision=2, highlight=True)

        assert entry["format"] == "bytes"
        assert entry["unit"] == "MB/s"
        assert entry["precision"] == 2
        assert entry["highlight"] is True

    def test_make_test_defaults_to_a_pass(self):
        assert make_test("boots") == {"name": "boots", "outcome": "pass"}

    def test_make_test_rounds_the_duration_and_keeps_the_detail(self):
        entry = make_test(
            "boots",
            outcome="fail",
            classname="smoke",
            duration_s=1.23456789,
            message="no response",
            traceback="line 1\nline 2",
        )

        assert entry["duration_s"] == 1.2346
        assert entry["classname"] == "smoke"
        assert entry["message"] == "no response"
        assert entry["traceback"] == "line 1\nline 2"

    def test_an_empty_message_is_omitted(self):
        assert "message" not in make_test("boots", message="")

    def test_a_passing_run_writes_an_empty_reason(self, tmp_path):
        write_verdict(tmp_path / "verdict.json", _result())

        payload = json.loads((tmp_path / "verdict.json").read_text())
        assert payload["passed"] is True
        assert payload["reason"] == ""
        assert payload["duration_s"] == 12.5
        assert payload["started_at_utc"].endswith("Z")

    def test_a_failing_run_gets_a_counted_reason_by_default(self, tmp_path):
        write_verdict(tmp_path / "verdict.json", _result(successes=1, failures=1))

        assert json.loads((tmp_path / "verdict.json").read_text())["reason"] == "1/2 iterations failed"

    def test_an_abort_reason_is_preferred_over_the_count(self, tmp_path):
        write_verdict(tmp_path / "verdict.json", _result(aborted=True, abort_reason="keyboard_interrupt"))

        assert json.loads((tmp_path / "verdict.json").read_text())["reason"] == "keyboard_interrupt"

    def test_an_explicit_reason_wins(self, tmp_path):
        write_verdict(tmp_path / "verdict.json", _result(failures=1), reason="thermal limit")

        assert json.loads((tmp_path / "verdict.json").read_text())["reason"] == "thermal limit"

    def test_results_and_tests_are_included_when_present(self, tmp_path):
        write_verdict(
            tmp_path / "verdict.json",
            _result(),
            results=[make_result("t", "T", 1)],
            tests=[make_test("boots")],
        )

        payload = json.loads((tmp_path / "verdict.json").read_text())
        assert payload["results"][0]["key"] == "t"
        assert payload["tests"][0]["name"] == "boots"

    def test_empty_results_and_tests_are_left_out(self, tmp_path):
        write_verdict(tmp_path / "verdict.json", _result(), results=[], tests=[])

        payload = json.loads((tmp_path / "verdict.json").read_text())
        assert "results" not in payload
        assert "tests" not in payload

    def test_a_simple_verdict_needs_no_run_result(self, tmp_path):
        write_simple_verdict(tmp_path / "nested" / "verdict.json", passed=False, reason="link down", frames=3)

        payload = json.loads((tmp_path / "nested" / "verdict.json").read_text())
        assert payload == {"passed": False, "reason": "link down", "frames": 3}


class TestSummary:
    def _write_verdict(self, tmp_path, **payload):
        (tmp_path / "verdict.json").write_text(json.dumps({"passed": True, **payload}))

    def test_no_verdict_means_no_summary(self, tmp_path):
        assert write_summary(tmp_path) is None
        assert not (tmp_path / "summary.md").exists()

    def test_an_unreadable_verdict_means_no_summary(self, tmp_path):
        (tmp_path / "verdict.json").write_text("{not json")

        assert write_summary(tmp_path) is None

    def test_a_verdict_that_is_not_a_mapping_means_no_summary(self, tmp_path):
        (tmp_path / "verdict.json").write_text("[1, 2]")

        assert write_summary(tmp_path) is None

    def test_the_heading_states_the_suite_and_outcome(self, tmp_path):
        self._write_verdict(tmp_path)

        body = write_summary(tmp_path, suite_name="thermal").read_text()

        assert body.startswith("# thermal — PASS")

    def test_a_failure_leads_with_its_reason(self, tmp_path):
        (tmp_path / "verdict.json").write_text(json.dumps({"passed": False, "reason": "too hot"}))

        body = write_summary(tmp_path, suite_name="thermal").read_text()

        assert "# thermal — FAIL" in body
        assert "**Reason:** too hot" in body

    def test_the_suite_name_falls_back_to_the_manifest(self, tmp_path):
        self._write_verdict(tmp_path)
        (tmp_path / "manifest.json").write_text(json.dumps({"suite": "from_manifest"}))

        assert write_summary(tmp_path).read_text().startswith("# from_manifest")

    def test_manifest_fields_appear_in_the_table(self, tmp_path):
        self._write_verdict(tmp_path, started_at_utc="2026-01-01T00:00:00Z")
        (tmp_path / "manifest.json").write_text(
            json.dumps({"run_id": "r1", "target": "unit-3", "unit_serial": "SN-9", "profile_path": "quick.yaml"})
        )

        body = write_summary(tmp_path).read_text()

        assert "| Run id | r1 |" in body
        assert "| Target | unit-3 |" in body
        assert "| Unit serial | SN-9 |" in body
        assert "| Started | 2026-01-01T00:00:00Z |" in body

    def test_iteration_counts_are_summarized_in_one_row(self, tmp_path):
        self._write_verdict(tmp_path, total_iterations=10, successes=9, failures=1)

        assert "| Iterations | 9 ok / 1 failed of 10 |" in write_summary(tmp_path).read_text()

    def test_a_verdict_without_counts_has_no_iterations_row(self, tmp_path):
        self._write_verdict(tmp_path)

        assert "| Iterations |" not in write_summary(tmp_path).read_text()

    @pytest.mark.parametrize(
        ("seconds", "rendered"),
        [(12.34, "12.3s"), (90, "1.5m"), (7200, "2.00h")],
    )
    def test_durations_scale_with_their_magnitude(self, tmp_path, seconds, rendered):
        self._write_verdict(tmp_path, duration_s=seconds)

        assert f"| Duration | {rendered} |" in write_summary(tmp_path).read_text()

    def test_a_non_numeric_duration_is_skipped(self, tmp_path):
        self._write_verdict(tmp_path, duration_s="a while")

        assert "| Duration |" not in write_summary(tmp_path).read_text()

    def test_headline_results_become_their_own_table(self, tmp_path):
        self._write_verdict(
            tmp_path,
            results=[make_result("throughput", "Throughput", 91.2, unit="MB/s"), "not a mapping"],
        )

        body = write_summary(tmp_path).read_text()

        assert "## Results" in body
        assert "| Throughput | 91.2 MB/s |" in body

    def test_a_result_without_a_label_falls_back_to_its_key(self, tmp_path):
        self._write_verdict(tmp_path, results=[{"key": "throughput", "value": 1}])

        assert "| throughput | 1 |" in write_summary(tmp_path).read_text()

    def test_the_profile_summary_is_rendered_sorted(self, tmp_path):
        self._write_verdict(tmp_path, profile_summary={"iterations": "2", "duration_s": "1.5"})

        body = write_summary(tmp_path).read_text()

        assert body.index("| duration_s | 1.5 |") < body.index("| iterations | 2 |")

    def test_the_profile_summary_can_come_from_the_manifest(self, tmp_path):
        self._write_verdict(tmp_path)
        (tmp_path / "manifest.json").write_text(json.dumps({"profile_summary": {"iterations": "2"}}))

        assert "| iterations | 2 |" in write_summary(tmp_path).read_text()
