"""The upset monitor, driven by a stand-in for a streaming instrument.

The monitor is stepped by hand rather than left to its thread, so a test
decides exactly which scans it has seen and when.
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any

import pytest

from gauntlet.capabilities import CapabilityRegistry, StreamSlice
from gauntlet.daq_recording import recordings, window
from gauntlet.supervisor.upsets import UpsetMonitor

PERIOD_S = 0.1


class _Stream:
    """A streaming provider whose scans a test pushes."""

    name = "daq"

    def __init__(self, instance: str = "daq0") -> None:
        self.instance = instance
        self.scans: list[tuple[int, float, float, list[float | None]]] = []
        self.leases = 0
        self.openable = True
        self.enabled_calls: list[tuple[dict[str, bool], int]] = []
        self._at = time.monotonic()

    def available(self) -> bool:
        return True

    def describe(self) -> dict[str, str]:
        return {"driver": "stub", "kind": "daq"}

    def instance_id(self) -> str:
        return self.instance

    def push(self, *values: float | None) -> None:
        self._at += PERIOD_S
        self.scans.append((len(self.scans) + 1, self._at, 1_700_000_000.0 + self._at, list(values)))

    def stream_channels(self) -> list[dict[str, Any]]:
        return [
            {"enabled": True, "key": "1", "label": "Rail", "unit": "V"},
            {"enabled": True, "key": "2", "label": "Aux", "unit": "V"},
        ]

    def stream_enable(self, enabled: dict[str, bool]) -> None:
        self.enabled_calls.append((dict(enabled), self.leases))

    def stream_close(self) -> None:
        self.leases -= 1

    def stream_open(self) -> bool:
        if self.openable:
            self.leases += 1
        return self.openable

    def stream_since(self, seq: int, limit: int) -> StreamSlice:
        scans = [scan for scan in self.scans if scan[0] >= seq][:limit]
        channels = [{"key": "1", "label": "Rail", "unit": "V"}, {"key": "2", "label": "Aux", "unit": "V"}]
        return StreamSlice(channels, 10.0, scans[-1][0] + 1 if scans else seq, scans)


class _NoThread:
    def join(self, timeout: float | None = None) -> None:
        return None


class _Rig:
    def __init__(self, tmp_path: Path, *names: str, lease: bool = True, initial: dict[str, Any] | None = None) -> None:
        self.registry = CapabilityRegistry()
        self.streams = {name: _Stream(name) for name in names}
        for name, stream in self.streams.items():
            self.registry.register(stream, role=name)
        self.run_dir = tmp_path
        self.lines: list[tuple[str, str]] = []
        self.published: list[dict[str, Any]] = []
        self.stops = 0
        self.monitor = UpsetMonitor(
            self.registry,
            [f"daq.{name}" for name in names],
            tmp_path,
            log_line=lambda level, message: self.lines.append((level, message)),
            publish=lambda kind, **payload: self.published.append({"type": kind, **payload}),
            stop_run=self._stop_run,
            initial=initial,
        )
        self.monitor._began = time.monotonic()
        for feed in self.monitor._feeds.values() if lease else []:
            self.monitor._lease(feed)

    def _stop_run(self) -> bool:
        self.stops += 1
        return True

    def limit(
        self, name: str, channel: str = "1", *, high: float | None = None, low: float | None = None, **window: float
    ) -> None:
        self.monitor.set_thresholds(f"daq.{name}", {channel: (low, high)}, **window)

    def step(self) -> None:
        for feed in self.monitor._feeds.values():
            self.monitor._follow(feed)

    def rows(self, index: int = 1) -> list[list[str]]:
        with (self.run_dir / "upsets" / f"upset_{index:04d}.csv").open() as file:
            return list(csv.reader(file))

    def summary(self) -> dict[str, Any]:
        self.monitor._flush(force=True)
        return json.loads((self.run_dir / "upsets.json").read_text())


@pytest.fixture
def rig(tmp_path: Path) -> _Rig:
    return _Rig(tmp_path, "a")


def _feed(rig: _Rig, *values: float, name: str = "a") -> None:
    for value in values:
        rig.streams[name].push(value, 0.0)
    rig.step()


class TestCrossing:
    def test_a_reading_above_the_limit_keeps_the_scans_around_it(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0.3, post_s=0.3)
        _feed(rig, 1, 1, 1, 1, 5, 1, 1, 1, 1)
        rows = rig.rows()
        assert rows[0] == ["t_s", "Rail", "Aux"]
        times = [float(row[0]) for row in rows[1:]]
        assert times == pytest.approx([-0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3])
        assert rows[4][1] == "5"

    def test_the_event_says_what_crossed_and_where(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0, post_s=0)
        _feed(rig, 1, 7)
        event = rig.summary()["events"][0]
        assert event["instrument"] == "daq.a"
        assert event["instance_id"] == "a"
        assert (event["channel"], event["label"], event["unit"]) == ("1", "Rail", "V")
        assert (event["direction"], event["limit"], event["value"]) == ("high", 2.0, 7.0)
        assert event["file"] == "upsets/upset_0001.csv"
        assert event["at"].endswith("Z") or "+00:00" in event["at"]
        assert event["truncated"] is False

    def test_a_reading_below_the_low_limit_is_a_low_upset(self, rig: _Rig) -> None:
        rig.limit("a", low=3.0, pre_s=0, post_s=0)
        _feed(rig, 3.3, 0.5)
        assert rig.summary()["events"][0]["direction"] == "low"

    def test_an_upset_is_logged_and_published(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0, post_s=0)
        _feed(rig, 9)
        assert rig.lines == [("WARN", "DAQ event #1 daq.a Rail 9V crossed high limit 2V")]
        assert [event["type"] for event in rig.published] == ["upset"]
        assert rig.published[0]["index"] == 1

    def test_a_signal_that_stays_outside_is_one_upset(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0.1, post_s=0.1)
        _feed(rig, *[5] * 100)
        assert len(rig.summary()["events"]) == 1

    def test_a_channel_rearms_when_the_reading_returns(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0, post_s=0.1)
        _feed(rig, 5, 5, 5, 1, 1, 5, 5, 1)
        assert len(rig.summary()["events"]) == 2

    def test_a_channel_does_not_rearm_while_its_window_is_open(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0, post_s=1.0)
        _feed(rig, 5, 1, 5, 1)
        assert rig.summary()["events"] == []
        _feed(rig, *[1] * 10)
        assert len(rig.summary()["events"]) == 1

    def test_two_channels_crossing_in_one_window_are_two_upsets(self, rig: _Rig) -> None:
        rig.monitor.set_thresholds("daq.a", {"1": (None, 2.0), "2": (None, 2.0)}, pre_s=0.1, post_s=0.3)
        rig.streams["a"].push(5, 0)
        rig.streams["a"].push(5, 5)
        for _ in range(5):
            rig.streams["a"].push(0, 0)
        rig.step()
        events = rig.summary()["events"]
        assert sorted(event["channel"] for event in events) == ["1", "2"]
        assert len(rig.rows(1)) > 2 and len(rig.rows(2)) > 2

    def test_a_crossing_near_the_start_has_no_padding(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=2.0, post_s=0.1)
        _feed(rig, 1, 5, 1, 1)
        times = [float(row[0]) for row in rig.rows()[1:]]
        assert times == pytest.approx([-0.1, 0.0, 0.1])

    def test_a_missing_reading_is_an_empty_cell_and_no_crossing(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0, post_s=0.1)
        rig.streams["a"].push(None, 0)
        rig.streams["a"].push(9, 0)
        rig.streams["a"].push(None, 0)
        rig.streams["a"].push(1, 0)
        rig.step()
        assert rig.rows()[-1][1] == ""

    def test_no_limit_means_no_upset(self, rig: _Rig) -> None:
        _feed(rig, 100, -100)
        assert rig.monitor.settings()["thresholds"]["daq.a"]["channels"] == {}
        assert rig.published == []

    def test_a_limit_changed_mid_run_applies_on_the_next_scan(self, rig: _Rig) -> None:
        rig.limit("a", high=50, pre_s=0, post_s=0)
        _feed(rig, 5)
        assert rig.published == []
        rig.limit("a", high=2.0, pre_s=0, post_s=0)
        _feed(rig, 5)
        assert len(rig.published) == 1

    def test_600_upsets_are_all_kept_when_stop_after_is_zero(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0, post_s=0)
        _feed(rig, *[5, 1] * 600)
        assert len(rig.summary()["events"]) == 600
        assert rig.stops == 0


class TestEndingTheWindow:
    def test_a_run_that_ends_inside_a_post_window_keeps_a_truncated_upset(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0, post_s=5.0)
        _feed(rig, 1, 5, 1)
        rig.monitor._thread = _NoThread()  # type: ignore[assignment]
        rig.monitor.stop()
        event = rig.summary()["events"][0]
        assert event["truncated"] is True
        assert len(rig.rows()) == 3

    def test_stopping_gives_the_leases_back(self, rig: _Rig) -> None:
        rig.monitor._thread = _NoThread()  # type: ignore[assignment]
        rig.monitor.stop()
        assert rig.streams["a"].leases == 0

    def test_a_scan_list_change_ends_the_open_upsets_truncated(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0, post_s=5.0)
        _feed(rig, 5)
        provider = rig.streams["a"]
        original = provider.stream_since

        def renamed(seq: int, limit: int) -> StreamSlice:
            streamed = original(seq, limit)
            return StreamSlice([{"key": "1", "label": "Other", "unit": "V"}], 10.0, streamed.next_seq, [])

        provider.stream_since = renamed  # type: ignore[method-assign]
        rig.step()
        assert rig.summary()["events"][0]["truncated"] is True


class TestWhatChangesUnderTheStream:
    def _relabel(self, rig: _Rig, *, keep_second: bool = True, label: str = "Rail") -> None:
        provider = rig.streams["a"]
        original = provider.stream_since

        def changed(seq: int, limit: int) -> StreamSlice:
            streamed = original(seq, limit)
            channels = [{"key": "1", "label": label, "unit": "V"}]
            if keep_second:
                channels.append({"key": "2", "label": "Aux", "unit": "V"})
            return StreamSlice(channels, streamed.rate_hz, streamed.next_seq, streamed.scans)

        provider.stream_since = changed  # type: ignore[method-assign]

    def test_a_channel_that_leaves_the_scan_list_loses_its_limits(self, rig: _Rig) -> None:
        rig.monitor.set_thresholds("daq.a", {"1": (None, 2.0), "2": (None, 2.0)})
        self._relabel(rig, keep_second=False)
        _feed(rig, 1)
        assert list(rig.monitor.settings()["thresholds"]["daq.a"]["channels"]) == ["1"]
        rig.monitor.set_thresholds("daq.a", {"1": (None, 3.0)})

    def test_renaming_a_channel_does_not_end_an_upset_or_make_a_second_one(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0, post_s=0.5)
        _feed(rig, 5)
        self._relabel(rig, label="Renamed")
        _feed(rig, 5, 5, 5, 5, 5, 5, 5)
        events = rig.summary()["events"]
        assert len(events) == 1
        assert events[0]["truncated"] is False

    def test_a_run_ending_inside_a_window_is_not_stopped_a_second_time(self, rig: _Rig) -> None:
        rig.monitor.set_stop_after(1)
        rig.limit("a", high=2.0, pre_s=0, post_s=5.0)
        _feed(rig, 5)
        rig.monitor._thread = _NoThread()  # type: ignore[assignment]
        rig.monitor.stop()
        assert rig.stops == 0
        assert rig.summary()["stopped_run"] is False


class TestRecordingEveryScan:
    def test_every_scan_the_stream_gives_is_kept_whatever_the_limits(self, rig: _Rig) -> None:
        _feed(rig, *range(50))
        (daq,) = recordings(rig.run_dir)["instruments"]
        assert (daq["instrument"], daq["rows"]) == ("daq.a", 50)
        assert [channel["label"] for channel in daq["channels"]] == ["Rail", "Aux"]

    def test_scans_are_kept_in_the_order_they_came(self, rig: _Rig) -> None:
        _feed(rig, 3, 1, 2)
        (part,) = window(rig.run_dir, "daq.a", 0, 100, 100)["segments"]
        assert [point[1] for point in part["points"]] == [3.0, 1.0, 2.0]

    def test_a_scan_list_that_changes_starts_a_new_segment(self, rig: _Rig) -> None:
        _feed(rig, 1, 2)
        TestWhatChangesUnderTheStream()._relabel(rig, keep_second=False)
        _feed(rig, 3)
        parts = window(rig.run_dir, "daq.a", 0, 100, 100)["segments"]
        assert [len(part["channels"]) for part in parts] == [2, 1]

    def test_a_disk_that_cannot_be_written_is_reported_once_and_watching_goes_on(
        self, rig: _Rig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*_a: Any, **_k: Any) -> None:
            raise OSError("no space left on device")

        monkeypatch.setattr(rig.monitor._feeds["daq.a"].recorder, "write", refuse)
        rig.limit("a", high=2.0, pre_s=0, post_s=0)
        _feed(rig, 5)
        _feed(rig, 1, 5)
        assert [message for level, message in rig.lines if level == "ERROR" and "could not record" in message] == [
            "DAQ monitor could not record daq.a: no space left on device"
        ]
        assert len(rig.published) == 2

    def test_stopping_closes_the_files(self, rig: _Rig) -> None:
        _feed(rig, 1, 2)
        rig.monitor._thread = _NoThread()  # type: ignore[assignment]
        rig.monitor.stop()
        assert rig.monitor._feeds["daq.a"].recorder._times is None


class TestStoppingTheRun:
    def test_stop_after_one_stops_the_run_once_after_the_post_window(self, rig: _Rig) -> None:
        rig.monitor.set_stop_after(1)
        rig.limit("a", high=2.0, pre_s=0, post_s=0.3)
        _feed(rig, 1, 5, 1)
        assert rig.stops == 0
        _feed(rig, 1, 1, 1, 1, 5, 1, 1, 1, 1, 1)
        assert rig.stops == 1
        summary = rig.summary()
        assert summary["stopped_run"] is True
        assert ("INFO", "stopping the run after 1 DAQ events") in rig.lines

    def test_stop_after_counts_upsets_from_every_instrument(self, tmp_path: Path) -> None:
        both = _Rig(tmp_path, "a", "b")
        both.monitor.set_stop_after(3)
        for name in ("a", "b"):
            both.limit(name, high=2.0, pre_s=0, post_s=0)
        _feed(both, 5, 1, 5, name="a")
        assert both.stops == 0
        _feed(both, 5, name="b")
        assert both.stops == 1
        _feed(both, 1, 5, name="b")
        assert both.stops == 1

    def test_stop_after_zero_never_stops_the_run(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0, post_s=0)
        _feed(rig, 5, 1, 5, 1, 5)
        assert rig.stops == 0

    def test_a_change_of_stop_after_applies_to_the_next_upset(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0, post_s=0)
        _feed(rig, 5, 1)
        rig.monitor.set_stop_after(2)
        _feed(rig, 5)
        assert rig.stops == 1

    def test_a_run_that_is_already_ending_is_left_alone(self, rig: _Rig) -> None:
        rig.monitor._stop_run = lambda: False
        rig.monitor.set_stop_after(1)
        rig.limit("a", high=2.0, pre_s=0, post_s=0)
        _feed(rig, 5)
        assert ("INFO", "the run was already ending") in rig.lines
        assert rig.summary()["stopped_run"] is False

    @pytest.mark.parametrize("count", [1001, -1, 1.5, True, "3"])
    def test_a_bad_stop_after_is_refused(self, rig: _Rig, count: Any) -> None:
        with pytest.raises(ValueError, match="stop_after"):
            rig.monitor.set_stop_after(count)


class TestThresholds:
    def test_an_unknown_instrument_is_refused(self, rig: _Rig) -> None:
        with pytest.raises(ValueError, match="not a streaming"):
            rig.monitor.set_thresholds("daq.zz", {})

    def test_an_unknown_channel_is_refused(self, rig: _Rig) -> None:
        with pytest.raises(ValueError, match="no channel"):
            rig.monitor.set_thresholds("daq.a", {"9": (None, 1.0)})

    @pytest.mark.parametrize("bound", [float("nan"), float("inf")])
    def test_a_limit_that_is_not_finite_is_refused(self, rig: _Rig, bound: float) -> None:
        with pytest.raises(ValueError, match="finite"):
            rig.monitor.set_thresholds("daq.a", {"1": (None, bound)})

    @pytest.mark.parametrize("window", [-1.0, 31.0, float("nan")])
    def test_a_window_outside_zero_to_thirty_is_refused(self, rig: _Rig, window: float) -> None:
        with pytest.raises(ValueError, match="pre_s"):
            rig.monitor.set_thresholds("daq.a", {}, pre_s=window)

    def test_a_channel_with_both_limits_empty_is_not_watched(self, rig: _Rig) -> None:
        rig.monitor.set_thresholds("daq.a", {"1": (None, None)})
        assert rig.monitor.settings()["thresholds"]["daq.a"]["channels"] == {}

    def test_the_settings_are_written_to_upsets_json(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, low=-1.0, pre_s=1.0, post_s=0.5)
        rig.monitor.set_stop_after(4)
        assert rig.summary()["thresholds"]["daq.a"] == {
            "channels": {"1": {"high": 2.0, "low": -1.0}},
            "post_s": 0.5,
            "pre_s": 1.0,
        }
        assert rig.summary()["stop_after"] == 4


class TestFailures:
    def test_a_failed_write_drops_the_upset_and_keeps_watching(self, rig: _Rig) -> None:
        (rig.run_dir / "upsets").write_text("in the way")
        rig.limit("a", high=2.0, pre_s=0, post_s=0)
        _feed(rig, 5, 1)
        assert [level for level, _ in rig.lines] == ["ERROR"]
        assert rig.published == []
        (rig.run_dir / "upsets").unlink()
        _feed(rig, 5)
        assert len(rig.published) == 1
        assert not list((rig.run_dir / "upsets").glob("*.tmp"))

    def test_a_stream_that_goes_quiet_warns_once_and_ends_the_open_upset(self, rig: _Rig) -> None:
        rig.limit("a", high=2.0, pre_s=0, post_s=5.0)
        _feed(rig, 5)
        feed = rig.monitor._feeds["daq.a"]
        feed.last_scan_at -= 60
        rig.step()
        rig.step()
        assert sum("lost the stream" in message for _, message in rig.lines) == 1
        assert rig.summary()["events"][0]["truncated"] is True
        assert rig.streams["a"].leases == 0

    def test_a_lost_stream_is_leased_again_when_the_instrument_returns(self, rig: _Rig) -> None:
        feed = rig.monitor._feeds["daq.a"]
        feed.last_scan_at -= 60
        rig.step()
        assert not feed.leased
        feed.retry_at = 0
        rig.step()
        assert feed.leased
        assert rig.streams["a"].leases == 1

    def test_an_instrument_that_will_not_stream_is_reported_once(self, tmp_path: Path) -> None:
        down = _Rig.__new__(_Rig)
        down.streams = {}
        stream = _Stream()
        stream.openable = False
        registry = CapabilityRegistry()
        registry.register(stream, role="a")
        lines: list[tuple[str, str]] = []
        monitor = UpsetMonitor(
            registry,
            ["daq.a"],
            tmp_path,
            log_line=lambda level, message: lines.append((level, message)),
            publish=lambda *_a, **_k: None,
            stop_run=lambda: True,
        )
        feed = monitor._feeds["daq.a"]
        monitor._lease(feed)
        monitor._lease(feed)
        assert len(lines) == 1


class TestWhatItFollows:
    def test_an_instrument_that_does_not_stream_is_not_followed(self, tmp_path: Path) -> None:
        class _Plain:
            name = "psu"

            def available(self) -> bool:
                return True

            def describe(self) -> dict[str, str]:
                return {}

            def instance_id(self) -> str:
                return "psu0"

        registry = CapabilityRegistry()
        registry.register(_Plain())
        monitor = UpsetMonitor(
            registry, ["psu"], tmp_path, log_line=lambda *_: None, publish=lambda *_a, **_k: None, stop_run=lambda: True
        )
        monitor.start()
        monitor.stop()
        assert monitor.followed() == []
        assert not (tmp_path / "upsets.json").exists()
        assert monitor.channel_keys("psu") is None

    def test_a_monitor_with_nothing_to_report_writes_no_file(self, rig: _Rig) -> None:
        rig.monitor._thread = _NoThread()  # type: ignore[assignment]
        rig.monitor.stop()
        assert not (rig.run_dir / "upsets.json").exists()

    def test_the_thread_finds_a_crossing_on_its_own(self, tmp_path: Path) -> None:
        rig = _Rig(tmp_path, "a", lease=False)
        rig.monitor.start()
        rig.limit("a", high=2.0, pre_s=0, post_s=0)
        rig.streams["a"].push(9, 0)
        deadline = time.monotonic() + 5
        while not rig.published and time.monotonic() < deadline:
            time.sleep(0.02)
        rig.monitor.stop()
        assert len(rig.published) == 1
        assert rig.streams["a"].leases == 0


_WAITS_FOR_STOP = """\
#!/usr/bin/env bash
run_dir="${GAUNTLET_RUN_DIR:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-dir) run_dir="$2"; shift 2 ;;
        *) shift ;;
    esac
done
mkdir -p "$run_dir"
trap 'echo "{\\"passed\\": true, \\"reason\\": \\"\\"}" > "$run_dir/verdict.json"; exit 0' USR1
while true; do sleep 0.05; done
"""


class TestInARun:
    def test_an_upset_with_stop_after_ends_the_run_and_the_suite_keeps_the_verdict(self, make_suite, settings) -> None:
        from fastapi.testclient import TestClient

        from gauntlet.app import create_app

        make_suite("waits", script=_WAITS_FOR_STOP)
        stream = _Stream()
        with TestClient(create_app(settings)) as client:
            client.app.state.capabilities.register(stream)
            started = client.post("/api/runs", json={"suite": "waits", "observe": ["daq"]})
            assert started.status_code == 201, started.text
            run_id = started.json()["run_id"]
            handle = client.app.state.supervisor.get(run_id)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not (handle.upsets and handle.upsets.channel_keys("daq")):
                time.sleep(0.02)
            assert handle.upsets is not None
            handle.upsets.set_stop_after(1)
            handle.upsets.set_thresholds("daq", {"1": (None, 2.0)}, pre_s=0, post_s=0)
            stream.push(9, 0)
            while time.monotonic() < deadline and client.get(f"/api/runs/{run_id}").json()["status"] not in {
                "passed",
                "failed",
                "aborted",
                "error",
            }:
                time.sleep(0.05)
            run = client.get(f"/api/runs/{run_id}").json()
            assert run["status"] == "passed"
            run_dir = Path(run["run_dir"])
            assert json.loads((run_dir / "upsets.json").read_text())["stopped_run"] is True
            log = (run_dir / "test.log").read_text()
            assert "WARN DAQ event #1 daq Rail 9V crossed high limit 2V" in log
            assert "INFO stopping the run after 1 DAQ events" in log
            assert stream.leases == 0


_FINISHES = """\
#!/usr/bin/env bash
run_dir="${GAUNTLET_RUN_DIR:-}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-dir) run_dir="$2"; shift 2 ;;
        *) shift ;;
    esac
done
mkdir -p "$run_dir"
echo '{"passed": true, "reason": ""}' > "$run_dir/verdict.json"
"""


@pytest.fixture
def live(make_suite, settings):
    """A client, a run waiting on the stub stream, and the stub."""
    from fastapi.testclient import TestClient

    from gauntlet.app import create_app

    make_suite("waits", script=_WAITS_FOR_STOP)
    make_suite("brief", script=_FINISHES)
    stream = _Stream()
    with TestClient(create_app(settings)) as client:
        client.app.state.capabilities.register(stream)
        started = client.post("/api/runs", json={"suite": "waits", "observe": ["daq"]})
        assert started.status_code == 201, started.text
        run_id = started.json()["run_id"]
        handle = client.app.state.supervisor.get(run_id)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (handle.upsets and handle.upsets.channel_keys("daq")):
            time.sleep(0.02)
        yield client, run_id, stream
        client.post(f"/api/runs/{run_id}/stop")
        while time.monotonic() < deadline and not handle.finished:
            time.sleep(0.05)


def _put(client: Any, run_id: str, **body: Any) -> Any:
    return client.put(f"/api/runs/{run_id}/upsets/thresholds", json={"instrument": "daq", **body})


class TestApi:
    def test_limits_set_on_a_run_in_flight_are_read_back(self, live) -> None:
        client, run_id, _ = live
        put = _put(client, run_id, channels={"1": {"high": 2.0}}, pre_s=1.0, post_s=0.5, stop_after=3)
        assert put.status_code == 200, put.text
        body = client.get(f"/api/runs/{run_id}/upsets").json()
        assert body["instruments"] == ["daq"]
        assert body["stop_after"] == 3
        assert body["events"] == []
        assert body["thresholds"]["daq"] == {
            "channels": {"1": {"high": 2.0, "low": None}},
            "post_s": 0.5,
            "pre_s": 1.0,
        }

    def _recorded(self, client: Any, run_id: str, rows: int) -> Any:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            found = client.get(f"/api/runs/{run_id}/daq").json()["instruments"]
            if found and found[0]["rows"] >= rows:
                return found[0]
            time.sleep(0.05)
        raise AssertionError("the scans were never recorded")

    def test_the_recording_is_listed_with_its_channels_and_span(self, live) -> None:
        client, run_id, stream = live
        for value in range(30):
            stream.push(float(value), 0.5)
        daq = self._recorded(client, run_id, 30)
        assert daq["instrument"] == "daq"
        assert daq["rows"] == 30
        assert [channel["label"] for channel in daq["channels"]] == ["Rail", "Aux"]
        assert daq["end_s"] == pytest.approx(2.9, abs=0.01)

    def test_a_window_of_the_recording_comes_back_at_full_rate(self, live) -> None:
        client, run_id, stream = live
        for value in range(30):
            stream.push(float(value), 0.5)
        self._recorded(client, run_id, 30)
        body = client.get(
            f"/api/runs/{run_id}/daq/data", params={"instrument": "daq", "start": 0.5, "end": 1.0, "points": 1000}
        ).json()
        (part,) = body["segments"]
        assert part["kind"] == "raw"
        assert [point[1] for point in part["points"]] == [5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

    def test_an_instrument_that_recorded_nothing_is_not_found(self, live) -> None:
        client, run_id, _ = live
        assert client.get(f"/api/runs/{run_id}/daq/data", params={"instrument": "psu"}).status_code == 404

    def test_an_unknown_run_has_no_recording(self, live) -> None:
        client, _, _ = live
        assert client.get("/api/runs/nope/daq").status_code == 404
        assert client.get("/api/runs/nope/daq/data", params={"instrument": "daq"}).status_code == 404

    def test_a_run_that_recorded_nothing_lists_nothing(self, make_suite, settings) -> None:
        from fastapi.testclient import TestClient

        from gauntlet.app import create_app

        make_suite("brief", script=_FINISHES)
        with TestClient(create_app(settings)) as client:
            started = client.post("/api/runs", json={"suite": "brief"}).json()["run_id"]
            assert client.get(f"/api/runs/{started}/daq").json() == {"instruments": [], "origin": None}

    def test_the_trace_can_be_asked_for_at_a_display_rate(self, live) -> None:
        client, run_id, stream = live
        for value in range(40):
            stream.push(float(value), 0.5)
        body = client.get(
            f"/api/runs/{run_id}/upsets/trace",
            params={"instrument": "daq", "since": 1, "display_hz": 5, "tail_s": 30},
        ).json()
        # The stub streams at 10 scans a second, so every second scan is kept.
        assert [scan[0] for scan in body["scans"]] == list(range(2, 41, 2))
        assert body["next_seq"] == 41

    def test_the_trace_is_the_scans_from_a_number_on(self, live) -> None:
        client, run_id, stream = live
        stream.push(1.5, 0.5)
        stream.push(2.5, 0.5)
        body = client.get(f"/api/runs/{run_id}/upsets/trace", params={"instrument": "daq", "since": 2}).json()
        assert set(body) == {"channels", "instrument", "next_seq", "rate_hz", "scans"}
        assert body["channels"] == [
            {"key": "1", "label": "Rail", "unit": "V"},
            {"key": "2", "label": "Aux", "unit": "V"},
        ]
        assert body["next_seq"] == 3
        assert [(scan[0], scan[2]) for scan in body["scans"]] == [(2, [2.5, 0.5])]

    def test_an_upset_is_listed_and_its_capture_is_served(self, live) -> None:
        client, run_id, stream = live
        _put(client, run_id, channels={"1": {"high": 2.0}}, pre_s=0, post_s=0)
        stream.push(9, 0)
        deadline = time.monotonic() + 10
        while (
            time.monotonic() < deadline
            and not (Path(client.get(f"/api/runs/{run_id}").json()["run_dir"]) / "upsets.json").exists()
        ):
            time.sleep(0.05)
        events = client.get(f"/api/runs/{run_id}/upsets").json()["events"]
        assert [event["index"] for event in events] == [1]
        capture = client.get(f"/api/runs/{run_id}/upsets/1")
        assert capture.status_code == 200
        assert capture.text.splitlines()[0] == "t_s,Rail,Aux"
        assert client.get(f"/api/runs/{run_id}/upsets/7").status_code == 404

    @pytest.mark.parametrize(
        "body",
        [
            {"channels": {"1": {"high": 1.0}}, "pre_s": 31},
            {"channels": {"1": {"high": 1.0}}, "post_s": -1},
            {"channels": {"9": {"high": 1.0}}},
            {"instrument": "telescope"},
            {"stop_after": 1001},
            {"stop_after": -1},
            {"stop_after": 1.5},
        ],
    )
    def test_a_bad_request_is_refused_and_changes_nothing(self, live, body: dict[str, Any]) -> None:
        client, run_id, _ = live
        assert _put(client, run_id, **body).status_code == 422
        assert client.get(f"/api/runs/{run_id}/upsets").json()["thresholds"]["daq"]["channels"] == {}

    def test_a_limit_that_is_not_finite_is_refused(self, live) -> None:
        client, run_id, _ = live
        refused = client.put(
            f"/api/runs/{run_id}/upsets/thresholds",
            content='{"instrument": "daq", "channels": {"1": {"high": NaN}}}',
            headers={"content-type": "application/json"},
        )
        assert refused.status_code == 422

    def test_a_trace_of_an_instrument_the_run_does_not_watch_is_not_found(self, live) -> None:
        client, run_id, _ = live
        assert client.get(f"/api/runs/{run_id}/upsets/trace", params={"instrument": "psu"}).status_code == 404

    def test_an_unknown_run_is_not_found(self, live) -> None:
        client, _, _ = live
        assert _put(client, "nope").status_code == 404
        assert client.get("/api/runs/nope/upsets").status_code == 404
        assert client.get("/api/runs/nope/upsets/trace", params={"instrument": "daq"}).status_code == 404

    def test_a_finished_run_answers_its_record_and_refuses_changes(self, make_suite, settings) -> None:
        from fastapi.testclient import TestClient

        from gauntlet.app import create_app

        make_suite("brief", script=_FINISHES)
        with TestClient(create_app(settings)) as client:
            started = client.post("/api/runs", json={"suite": "brief"}).json()["run_id"]
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and client.get(f"/api/runs/{started}").json()["status"] != "passed":
                time.sleep(0.05)
            assert client.get(f"/api/runs/{started}/upsets").json() == {
                "events": [],
                "instruments": [],
                "stop_after": 0,
                "stopped_run": False,
                "thresholds": {},
            }
            assert _put(client, started).status_code == 409
            assert client.get(f"/api/runs/{started}/upsets/trace", params={"instrument": "daq"}).status_code == 409


class TestLimitsAtStart:
    def test_limits_given_with_the_run_are_in_force_from_its_first_scan(self, tmp_path: Path) -> None:
        rig = _Rig(
            tmp_path,
            "a",
            lease=False,
            initial={
                "stop_after": 2,
                "instruments": {"daq.a": {"channels": {"1": (None, 2.0)}, "pre_s": 0.5, "post_s": 0.25}},
            },
        )
        rig.monitor.start()
        rig.monitor._stop.set()
        assert rig.monitor.settings()["stop_after"] == 2
        assert rig.monitor.settings()["thresholds"]["daq.a"] == {
            "channels": {"1": {"high": 2.0, "low": None}},
            "post_s": 0.25,
            "pre_s": 0.5,
        }
        rig.monitor.stop()

    def test_channels_the_run_was_started_with_are_enabled_before_the_stream_is_leased(self, tmp_path: Path) -> None:
        rig = _Rig(
            tmp_path,
            "a",
            lease=False,
            initial={
                "instruments": {"daq.a": {"enabled": {"1": True, "2": False}, "channels": {}}},
            },
        )
        rig.monitor.start()
        rig.monitor.stop()
        assert rig.streams["a"].enabled_calls == [({"1": True, "2": False}, 0)]

    def test_channels_that_cannot_be_enabled_are_reported_not_fatal(self, tmp_path: Path) -> None:
        rig = _Rig(
            tmp_path,
            "a",
            lease=False,
            initial={
                "instruments": {"daq.a": {"enabled": {"1": False}, "channels": {}}},
            },
        )

        def refuse(enabled: dict[str, bool]) -> None:
            raise ValueError("daq: at least one channel must stay enabled")

        rig.streams["a"].stream_enable = refuse  # type: ignore[method-assign]
        rig.monitor.start()
        rig.monitor.stop()
        assert any(level == "WARN" and "at least one channel" in message for level, message in rig.lines)

    def test_limits_for_an_instrument_the_run_does_not_watch_are_reported_not_fatal(self, tmp_path: Path) -> None:
        rig = _Rig(tmp_path, "a", lease=False, initial={"instruments": {"daq.zz": {"channels": {}}}})
        rig.monitor.start()
        rig.monitor.stop()
        assert any(level == "WARN" and "daq.zz" in message for level, message in rig.lines)

    def test_a_run_started_with_limits_keeps_them(self, make_suite, settings) -> None:
        from fastapi.testclient import TestClient

        from gauntlet.app import create_app

        make_suite("waits", script=_WAITS_FOR_STOP)
        with TestClient(create_app(settings)) as client:
            client.app.state.capabilities.register(_Stream())
            started = client.post(
                "/api/runs",
                json={
                    "suite": "waits",
                    "observe": ["daq"],
                    "upsets": {
                        "stop_after": 3,
                        "instruments": {"daq": {"channels": {"1": {"high": 2.5}}, "pre_s": 1, "post_s": 1}},
                    },
                },
            )
            assert started.status_code == 201, started.text
            run_id = started.json()["run_id"]
            deadline = time.monotonic() + 10
            body = {}
            while time.monotonic() < deadline and not body.get("thresholds", {}).get("daq", {}).get("channels"):
                body = client.get(f"/api/runs/{run_id}/upsets").json()
                time.sleep(0.05)
            client.post(f"/api/runs/{run_id}/stop")
        assert body["stop_after"] == 3
        assert body["thresholds"]["daq"]["channels"] == {"1": {"high": 2.5, "low": None}}

    def test_a_run_started_with_channels_turned_off_asks_the_instrument_to_turn_them_off(
        self, make_suite, settings
    ) -> None:
        from fastapi.testclient import TestClient

        from gauntlet.app import create_app

        make_suite("waits", script=_WAITS_FOR_STOP)
        stream = _Stream()
        with TestClient(create_app(settings)) as client:
            client.app.state.capabilities.register(stream)
            started = client.post(
                "/api/runs",
                json={
                    "suite": "waits",
                    "observe": ["daq"],
                    "upsets": {"instruments": {"daq": {"enabled": {"2": False}}}},
                },
            )
            assert started.status_code == 201, started.text
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not stream.enabled_calls:
                time.sleep(0.05)
            client.post(f"/api/runs/{started.json()['run_id']}/stop")
        assert stream.enabled_calls == [({"2": False}, 0)]

    @pytest.mark.parametrize(
        "upsets",
        [
            {"stop_after": 1001},
            {"instruments": {"daq": {"pre_s": 31}}},
            {"instruments": {"daq": {"channels": {"1": {"high": float("inf")}}}}},
        ],
    )
    def test_limits_that_could_not_be_applied_refuse_the_run(
        self, make_suite, settings, upsets: dict[str, Any]
    ) -> None:
        from fastapi.testclient import TestClient

        from gauntlet.app import create_app

        make_suite("waits", script=_WAITS_FOR_STOP)
        with TestClient(create_app(settings)) as client:
            refused = client.post(
                "/api/runs",
                content=json.dumps({"suite": "waits", "upsets": upsets}),
                headers={"content-type": "application/json"},
            )
            assert refused.status_code == 422
            assert client.get("/api/runs").json()["total"] == 0


class TestDisplayRate:
    def _stream_of(self, rig: _Rig, count: int, rate_hz: float = 1000.0) -> None:
        provider = rig.streams["a"]
        original = provider.stream_since

        def faster(seq: int, limit: int) -> StreamSlice:
            streamed = original(seq, limit)
            return StreamSlice(streamed.channels, rate_hz, streamed.next_seq, streamed.scans)

        provider.stream_since = faster  # type: ignore[method-assign]
        for value in range(count):
            provider.push(float(value), 0.0)

    def test_a_trace_can_be_thinned_to_a_display_rate_without_losing_its_place(self, rig: _Rig) -> None:
        self._stream_of(rig, 100)
        thinned = rig.monitor.trace("daq.a", 1, display_hz=250)
        assert [scan[0] for scan in thinned.scans] == [seq for seq in range(1, 101) if seq % 4 == 0]
        assert thinned.next_seq == 101

    def test_a_trace_slower_than_the_display_rate_is_not_thinned(self, rig: _Rig) -> None:
        self._stream_of(rig, 20, rate_hz=10.0)
        assert len(rig.monitor.trace("daq.a", 1, display_hz=50).scans) == 20

    def test_a_trace_can_start_from_the_last_seconds_instead_of_the_oldest_scan(self, rig: _Rig) -> None:
        self._stream_of(rig, 100)
        recent = rig.monitor.trace("daq.a", 1, tail_s=2.95)
        assert [scan[0] for scan in recent.scans] == list(range(71, 101))
        assert recent.next_seq == 101

    def test_the_tail_is_only_for_the_first_request(self, rig: _Rig) -> None:
        self._stream_of(rig, 100)
        assert rig.monitor.trace("daq.a", 90, tail_s=3.0).scans[0][0] == 90


class TestInstrumentList:
    def test_a_streaming_instrument_says_its_channels_and_rate(self, make_suite, settings) -> None:
        from fastapi.testclient import TestClient

        from gauntlet.app import create_app

        with TestClient(create_app(settings)) as client:
            client.app.state.capabilities.register(_Stream())
            entry = next(i for i in client.get("/api/instruments").json()["instruments"] if i["name"] == "daq")
        assert entry["stream"] == {
            "channels": [
                {"enabled": True, "key": "1", "label": "Rail", "unit": "V"},
                {"enabled": True, "key": "2", "label": "Aux", "unit": "V"},
            ],
            "rate_hz": 10.0,
        }

    def test_an_instrument_that_does_not_stream_says_nothing_of_it(self, settings) -> None:
        from fastapi.testclient import TestClient

        from gauntlet.app import create_app

        with TestClient(create_app(settings)) as client:
            entries = client.get("/api/instruments").json()["instruments"]
        assert entries and all("stream" not in entry for entry in entries)
