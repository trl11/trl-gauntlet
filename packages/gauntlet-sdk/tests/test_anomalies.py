"""Anomaly recording, which counts separately from iteration results."""

from __future__ import annotations

import json

import pytest

from gauntlet_sdk import AnomalyLog, JsonlSink


@pytest.fixture
def sink(tmp_path):
    written = JsonlSink(tmp_path / "metrics.jsonl")
    yield written
    written.close()


def _records(sink: JsonlSink) -> list[dict]:
    return [json.loads(line) for line in sink.path.read_text().splitlines() if line]


class TestAnomalyLog:
    def test_a_recorded_anomaly_reaches_the_sink(self, sink):
        AnomalyLog(sink).record("uut_monitor", "sample_failed")

        record = _records(sink)[0]
        assert record["kind"] == "anomaly"
        assert record["probe"] == "uut_monitor"
        assert record["anomaly_kind"] == "sample_failed"

    def test_detail_and_iteration_travel_together_in_the_payload(self, sink):
        AnomalyLog(sink).record("link", "frame_lost", iteration=7, detail={"expected": 41})

        assert _records(sink)[0]["detail"] == {"expected": 41, "iteration": 7}

    def test_iteration_is_omitted_when_not_given(self, sink):
        AnomalyLog(sink).record("link", "frame_lost", detail={"expected": 41})

        assert _records(sink)[0]["detail"] == {"expected": 41}

    def test_the_caller_detail_dict_is_not_mutated(self, sink):
        detail = {"expected": 41}
        AnomalyLog(sink).record("link", "frame_lost", iteration=2, detail=detail)

        assert detail == {"expected": 41}

    def test_counts_are_kept_per_probe(self, sink):
        log = AnomalyLog(sink)
        log.record("link", "frame_lost")
        log.record("link", "frame_lost")
        log.record("psu", "over_current")

        assert log.counts() == {"link": 2, "psu": 1}
        assert log.total() == 3

    def test_an_untouched_log_counts_nothing(self, sink):
        log = AnomalyLog(sink)

        assert log.counts() == {}
        assert log.total() == 0

    def test_counts_are_a_copy_the_caller_cannot_corrupt(self, sink):
        log = AnomalyLog(sink)
        log.record("link", "frame_lost")

        log.counts()["link"] = 99

        assert log.total() == 1
