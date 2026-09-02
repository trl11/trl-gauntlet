"""Turning a suite's stdout and metrics file into live events."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from gauntlet.supervisor.events import EventBus
from gauntlet.supervisor.readers import (
    classify_log_line,
    flatten,
    publish_record,
    pump_stdout,
    tail_metrics,
)


@pytest.fixture
def bus():
    return EventBus()


def _events(bus: EventBus, kind: str | None = None) -> list[dict]:
    records = [event.to_dict() for event in bus.history()]
    return [record for record in records if kind is None or record["type"] == kind]


def _spawn(script: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(script)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


class TestClassifyLogLine:
    def test_an_ordinary_line_is_info(self):
        assert classify_log_line("iter 1: ok") == ("info", "iter 1: ok")

    def test_an_error_prefix_is_stripped(self):
        assert classify_log_line("error: link down") == ("error", "link down")

    def test_a_warn_prefix_is_stripped(self):
        assert classify_log_line("warn: link flaky") == ("warning", "link flaky")

    def test_the_prefix_match_is_case_insensitive(self):
        assert classify_log_line("ERROR: link down") == ("error", "link down")

    def test_a_whole_word_elsewhere_still_raises_the_level(self):
        assert classify_log_line("run FAILED after 3 iterations")[0] == "error"

    def test_a_traceback_is_an_error(self):
        assert classify_log_line("Traceback (most recent call last):")[0] == "error"

    def test_a_warning_word_elsewhere_raises_the_level(self):
        assert classify_log_line("this is a WARNING about the link")[0] == "warning"

    def test_a_word_that_merely_contains_error_is_left_alone(self):
        assert classify_log_line("errors_total=0")[0] == "info"

    def test_a_line_without_a_prefix_keeps_its_text(self):
        assert classify_log_line("FAILED") == ("error", "FAILED")


class TestFlatten:
    def test_nested_keys_become_dotted_paths(self):
        assert flatten({"uut": {"load_1m": 0.5}}) == [("uut.load_1m", 0.5)]

    def test_non_scalar_leaves_are_dropped(self):
        assert flatten({"name": "unit-3", "count": 2}) == [("count", 2)]

    def test_a_scalar_without_a_path_is_dropped(self):
        assert flatten(1) == []

    def test_an_empty_mapping_flattens_to_nothing(self):
        assert flatten({}) == []


class TestPublishRecord:
    def test_an_iteration_publishes_metrics_and_an_iteration_event(self, bus):
        publish_record(bus, '{"kind":"iteration","iteration":2,"success":true,"metrics":{"v":1.5}}')

        assert _events(bus, "metrics")[0]["values"] == {"v": 1.5}
        assert _events(bus, "iteration")[0]["iteration"] == 2
        assert _events(bus, "iteration")[0]["success"] is True

    def test_the_kind_defaults_to_iteration(self, bus):
        publish_record(bus, '{"iteration":1,"success":false,"reason":"too hot"}')

        assert _events(bus, "iteration")[0]["reason"] == "too hot"

    def test_a_record_without_metrics_publishes_no_metrics_event(self, bus):
        publish_record(bus, '{"iteration":1,"success":true}')

        assert _events(bus, "metrics") == []

    def test_non_numeric_metrics_are_left_out_of_the_values(self, bus):
        publish_record(bus, '{"iteration":1,"success":true,"metrics":{"v":1,"label":"x"}}')

        assert _events(bus, "metrics")[0]["values"] == {"v": 1.0}

    def test_metrics_that_are_not_a_mapping_are_ignored(self, bus):
        publish_record(bus, '{"iteration":1,"success":true,"metrics":[1,2]}')

        assert _events(bus, "metrics") == []
        assert _events(bus, "iteration")

    def test_a_live_record_publishes_metrics_but_no_iteration(self, bus):
        publish_record(bus, '{"kind":"live","metrics":{"uut":{"load_1m":0.5}}}')

        assert _events(bus, "metrics")[0]["values"] == {"uut.load_1m": 0.5}
        assert _events(bus, "iteration") == []

    def test_an_anomaly_publishes_an_anomaly_and_no_iteration(self, bus):
        publish_record(bus, '{"kind":"anomaly","probe":"link","anomaly_kind":"frame_lost","detail":{"n":2}}')

        anomaly = _events(bus, "anomaly")[0]
        assert anomaly["probe"] == "link"
        assert anomaly["anomaly_kind"] == "frame_lost"
        assert anomaly["detail"] == {"n": 2}
        assert _events(bus, "iteration") == []

    def test_an_anomaly_without_a_probe_still_publishes(self, bus):
        publish_record(bus, '{"kind":"anomaly"}')

        assert _events(bus, "anomaly")[0]["probe"] == "?"

    def test_each_phase_becomes_its_own_event(self, bus):
        publish_record(
            bus,
            '{"iteration":1,"success":true,"phases":['
            '{"name":"boot","elapsed_s":1.5,"success":true,"detail":{"host":"unit-3"}},'
            '{"name":"measure","elapsed_s":0.5,"success":false}]}',
        )

        phases = _events(bus, "phase")
        assert [p["phase"] for p in phases] == ["boot", "measure"]
        assert phases[0]["detail"] == {"host": "unit-3"}
        assert phases[1]["success"] is False

    def test_a_phase_that_is_not_a_mapping_is_skipped(self, bus):
        publish_record(bus, '{"iteration":1,"success":true,"phases":["boot"]}')

        assert _events(bus, "phase") == []

    def test_image_paths_travel_with_the_iteration(self, bus):
        publish_record(bus, '{"iteration":1,"success":true,"metrics":{"images":["frames/a.png",2]}}')

        assert _events(bus, "iteration")[0]["images"] == ["frames/a.png"]

    def test_images_that_are_not_a_list_are_ignored(self, bus):
        publish_record(bus, '{"iteration":1,"success":true,"metrics":{"images":"a.png"}}')

        assert _events(bus, "iteration")[0]["images"] == []

    def test_trace_paths_travel_beside_the_images(self, bus):
        publish_record(
            bus,
            '{"iteration":1,"success":true,"metrics":{"images":["frames/a.png"],"traces":["traces/a.png"]}}',
        )

        iteration = _events(bus, "iteration")[0]
        assert iteration["images"] == ["frames/a.png"]
        assert iteration["traces"] == ["traces/a.png"]

    def test_an_iteration_naming_no_traces_carries_none(self, bus):
        publish_record(bus, '{"iteration":1,"success":true}')

        assert _events(bus, "iteration")[0]["traces"] == []

    def test_a_line_that_is_not_json_is_dropped(self, bus):
        publish_record(bus, "not json at all")

        assert _events(bus) == []

    def test_json_that_is_not_an_object_is_dropped(self, bus):
        publish_record(bus, "[1, 2]")

        assert _events(bus) == []


class TestPumpStdout:
    def test_every_line_reaches_the_bus_and_the_log_file(self, bus, tmp_path):
        proc = _spawn(
            """
            print("iter 1: ok")
            print("error: link down")
            """
        )
        pump_stdout(proc, bus, tmp_path / "test.log")
        proc.wait()

        levels = [(event["level"], event["message"]) for event in _events(bus, "log")]
        assert levels == [("info", "iter 1: ok"), ("error", "link down")]
        assert (tmp_path / "test.log").read_text() == "iter 1: ok\nerror: link down\n"

    def test_blank_lines_are_dropped(self, bus, tmp_path):
        proc = _spawn(
            """
            print("")
            print("done")
            """
        )
        pump_stdout(proc, bus, tmp_path / "test.log")
        proc.wait()

        assert [event["message"] for event in _events(bus, "log")] == ["done"]

    def test_colour_escapes_are_stripped(self, bus, tmp_path):
        proc = _spawn(
            r"""
            print("\x1b[31mred\x1b[0m text")
            """
        )
        pump_stdout(proc, bus, tmp_path / "test.log")
        proc.wait()

        assert _events(bus, "log")[0]["message"] == "red text"

    def test_a_process_without_stdout_is_a_no_op(self, bus, tmp_path):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        pump_stdout(proc, bus, tmp_path / "test.log")
        proc.wait()

        assert _events(bus) == []

    def test_an_unwritable_log_path_does_not_stop_the_stream(self, bus, tmp_path):
        proc = _spawn(
            """
            print("still streaming")
            """
        )
        pump_stdout(proc, bus, tmp_path / "missing-dir" / "test.log")
        proc.wait()

        assert _events(bus, "log")[0]["message"] == "still streaming"


class TestTailMetrics:
    def test_records_written_during_the_run_are_published(self, bus, tmp_path):
        metrics = tmp_path / "metrics.jsonl"
        proc = _spawn(
            f"""
            import time
            path = {str(metrics)!r}
            with open(path, "w", buffering=1) as fh:
                fh.write('{{"iteration":1,"success":true}}\\n')
                time.sleep(0.4)
                fh.write('{{"iteration":2,"success":true}}\\n')
            """
        )
        tail_metrics(metrics, proc, bus, poll_s=0.05)
        proc.wait()

        assert [event["iteration"] for event in _events(bus, "iteration")] == [1, 2]

    def test_a_final_flush_after_exit_is_still_picked_up(self, bus, tmp_path):
        metrics = tmp_path / "metrics.jsonl"
        proc = _spawn(
            f"""
            path = {str(metrics)!r}
            with open(path, "w") as fh:
                fh.write('{{"iteration":1,"success":true}}\\n')
            """
        )
        tail_metrics(metrics, proc, bus, poll_s=0.05)
        proc.wait()

        assert [event["iteration"] for event in _events(bus, "iteration")] == [1]

    def test_a_partial_final_line_is_not_published(self, bus, tmp_path):
        metrics = tmp_path / "metrics.jsonl"
        proc = _spawn(
            f"""
            path = {str(metrics)!r}
            with open(path, "w") as fh:
                fh.write('{{"iteration":1,"success":true}}\\n')
                fh.write('{{"iteration":2,')
            """
        )
        tail_metrics(metrics, proc, bus, poll_s=0.05)
        proc.wait()

        assert [event["iteration"] for event in _events(bus, "iteration")] == [1]

    def test_a_run_that_writes_no_metrics_publishes_nothing(self, bus, tmp_path):
        proc = _spawn("pass")
        tail_metrics(tmp_path / "metrics.jsonl", proc, bus, poll_s=0.05)
        proc.wait()

        assert _events(bus) == []
