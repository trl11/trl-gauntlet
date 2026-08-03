"""Background sampling of the unit under test."""

from __future__ import annotations

import json
import threading

import pytest

from gauntlet_sdk import AnomalyLog, JsonlSink, RemoteError, RemoteMonitor, RemoteTarget


class FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def sink(tmp_path):
    written = JsonlSink(tmp_path / "metrics.jsonl")
    yield written
    written.close()


def _records(sink: JsonlSink) -> list[dict]:
    return [json.loads(line) for line in sink.path.read_text().splitlines() if line]


class Harness:
    """Stands in for the SSH side, and signals when a sample was attempted."""

    def __init__(self, monkeypatch, *, metrics=None, connect_error=None, sample_error=None):
        self.clients: list[FakeClient] = []
        self.attempts = 0
        self.sampled = threading.Event()
        self._metrics = {"load_1m": 0.5} if metrics is None else metrics
        self._connect_error = connect_error
        self._sample_error = sample_error
        monkeypatch.setattr("gauntlet_sdk.monitor.connect", self._connect)
        monkeypatch.setattr("gauntlet_sdk.monitor.sample_host_metrics", self._sample)

    def _connect(self, _target, **_kwargs):
        if self._connect_error is not None:
            self.attempts += 1
            self.sampled.set()
            raise self._connect_error
        client = FakeClient()
        self.clients.append(client)
        return client

    def _sample(self, _client, **_kwargs):
        self.attempts += 1
        try:
            if self._sample_error is not None:
                raise self._sample_error
            return self._metrics
        finally:
            self.sampled.set()

    def wait(self):
        assert self.sampled.wait(timeout=5.0), "the monitor never sampled"


def _monitor(sink, **kwargs) -> RemoteMonitor:
    return RemoteMonitor(RemoteTarget(host="unit-3"), sink, period_s=0.5, **kwargs)


class TestRemoteMonitor:
    def test_samples_are_written_as_live_records(self, monkeypatch, sink):
        harness = Harness(monkeypatch, metrics={"load_1m": 0.5})
        monitor = _monitor(sink)

        monitor.start()
        harness.wait()
        monitor.stop()

        assert monitor.samples >= 1
        assert monitor.failures == 0
        record = _records(sink)[0]
        assert record["kind"] == "live"
        assert record["metrics"] == {"uut": {"load_1m": 0.5}}

    def test_an_empty_sample_is_not_recorded(self, monkeypatch, sink):
        harness = Harness(monkeypatch, metrics={})
        monitor = _monitor(sink)

        monitor.start()
        harness.wait()
        monitor.stop()

        assert monitor.samples == 0
        assert _records(sink) == []

    def test_the_connection_is_closed_when_sampling_stops(self, monkeypatch, sink):
        harness = Harness(monkeypatch)
        monitor = _monitor(sink)

        monitor.start()
        harness.wait()
        monitor.stop()

        assert harness.clients[0].closed

    def test_a_connection_is_reused_across_ticks(self, monkeypatch, sink):
        harness = Harness(monkeypatch)
        monitor = RemoteMonitor(RemoteTarget(host="unit-3"), sink, period_s=0.0)

        monitor.start()
        harness.wait()
        monitor.stop()

        assert len(harness.clients) == 1

    def test_a_failed_sample_is_counted_and_recorded_as_an_anomaly(self, monkeypatch, sink):
        harness = Harness(monkeypatch, sample_error=RemoteError("connection reset"))
        anomalies = AnomalyLog(sink)
        monitor = _monitor(sink, anomalies=anomalies, probe_name="uut")

        monitor.start()
        harness.wait()
        monitor.stop()

        assert monitor.failures >= 1
        assert monitor.samples == 0
        assert anomalies.counts()["uut"] >= 1
        record = _records(sink)[0]
        assert record["anomaly_kind"] == "sample_failed"
        assert "RemoteError: connection reset" in record["detail"]["error"]

    def test_a_failed_sample_drops_the_connection(self, monkeypatch, sink):
        harness = Harness(monkeypatch, sample_error=OSError("broken pipe"))
        monitor = _monitor(sink)

        monitor.start()
        harness.wait()
        monitor.stop()

        assert harness.clients[0].closed

    def test_a_failure_to_connect_is_counted_without_an_anomaly_log(self, monkeypatch, sink):
        harness = Harness(monkeypatch, connect_error=RemoteError("no route to host"))
        monitor = _monitor(sink)

        monitor.start()
        harness.wait()
        monitor.stop()

        assert monitor.failures >= 1
        assert _records(sink) == []

    def test_starting_twice_does_not_start_a_second_thread(self, monkeypatch, sink):
        harness = Harness(monkeypatch)
        monitor = _monitor(sink)

        monitor.start()
        monitor.start()
        harness.wait()
        monitor.stop()

        assert len(harness.clients) == 1

    def test_stopping_without_starting_is_harmless(self, sink):
        _monitor(sink).stop()

    def test_stopping_twice_is_harmless(self, monkeypatch, sink):
        harness = Harness(monkeypatch)
        monitor = _monitor(sink)

        monitor.start()
        harness.wait()
        monitor.stop()
        monitor.stop()
