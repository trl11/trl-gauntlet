"""Keeping every scan of a streaming instrument, and reading a window of it back."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from gauntlet.daq_recording import Recorder, recordings, window

T0 = 1_700_000_000.0
TWO = [{"key": "1", "label": "Rail", "unit": "V"}, {"key": "2", "label": "Aux", "unit": "V"}]


def scans(count: int, *, first: int = 0, period: float = 0.01, start: float = T0) -> list:
    """Scans whose first channel counts up and whose second is its negative."""
    return [
        (first + i + 1, 0.0, start + (first + i) * period, [float(first + i), -float(first + i)]) for i in range(count)
    ]


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path


def record(run_dir: Path, count: int = 100, instrument: str = "daq.0", channels=TWO) -> None:
    recorder = Recorder(run_dir, instrument)
    recorder.write(channels, 100.0, scans(count))
    recorder.close()


class TestWhatIsKept:
    def test_every_scan_is_kept_at_its_own_time(self, run_dir: Path) -> None:
        record(run_dir)
        found = recordings(run_dir)
        assert found["origin"] == pytest.approx(T0)
        (daq,) = found["instruments"]
        assert (daq["instrument"], daq["rows"], daq["rate_hz"]) == ("daq.0", 100, 100.0)
        assert daq["start_s"] == pytest.approx(0.0)
        assert daq["end_s"] == pytest.approx(0.99)
        assert [channel["label"] for channel in daq["channels"]] == ["Rail", "Aux"]

    def test_a_run_that_recorded_nothing_has_no_recording(self, run_dir: Path) -> None:
        assert recordings(run_dir) == {"instruments": [], "origin": None}

    def test_scans_written_in_pieces_join_up(self, run_dir: Path) -> None:
        recorder = Recorder(run_dir, "daq.0")
        recorder.write(TWO, 100.0, scans(40))
        recorder.write(TWO, 100.0, scans(60, first=40))
        recorder.close()
        assert recordings(run_dir)["instruments"][0]["rows"] == 100

    def test_a_reading_the_instrument_did_not_give_is_nan(self, run_dir: Path) -> None:
        recorder = Recorder(run_dir, "daq.0")
        recorder.write(TWO, 100.0, [(1, 0.0, T0, [1.0, None])])
        recorder.close()
        (part,) = window(run_dir, "daq.0", 0, 1, 100)["segments"]
        assert part["points"][0][1] == 1.0
        assert math.isnan(part["points"][0][2])

    def test_a_scan_list_that_changes_starts_a_new_segment(self, run_dir: Path) -> None:
        recorder = Recorder(run_dir, "daq.0")
        recorder.write(TWO, 100.0, scans(10))
        recorder.write(TWO[:1], 200.0, [(11, 0.0, T0 + 0.5, [5.0]), (12, 0.0, T0 + 0.505, [6.0])])
        recorder.close()
        parts = window(run_dir, "daq.0", 0, 10, 100)["segments"]
        assert [len(part["channels"]) for part in parts] == [2, 1]
        assert [part["rate_hz"] for part in parts] == [100.0, 200.0]

    def test_a_rename_updates_the_labels_without_a_new_segment(self, run_dir: Path) -> None:
        recorder = Recorder(run_dir, "daq.0")
        recorder.write(TWO, 100.0, scans(5))
        renamed = [{**TWO[0], "label": "Rail 3V3"}, TWO[1]]
        recorder.write(renamed, 100.0, scans(5, first=5))
        recorder.close()
        parts = window(run_dir, "daq.0", 0, 10, 100)["segments"]
        assert len(parts) == 1
        assert parts[0]["channels"][0]["label"] == "Rail 3V3"

    def test_two_instruments_are_kept_apart(self, run_dir: Path) -> None:
        record(run_dir, 10, "daq.0")
        record(run_dir, 20, "daq.1")
        assert {entry["instrument"]: entry["rows"] for entry in recordings(run_dir)["instruments"]} == {
            "daq.0": 10,
            "daq.1": 20,
        }


class TestWindows:
    def test_a_small_window_is_every_scan_in_it(self, run_dir: Path) -> None:
        record(run_dir)
        (part,) = window(run_dir, "daq.0", 0.10, 0.19, 1000)["segments"]
        assert part["kind"] == "raw"
        assert [point[1] for point in part["points"]] == [float(n) for n in range(10, 20)]
        assert part["points"][0][0] == pytest.approx(0.10)
        assert part["points"][0][2] == -10.0

    def test_the_edges_are_inclusive(self, run_dir: Path) -> None:
        record(run_dir)
        (part,) = window(run_dir, "daq.0", 0.10, 0.12, 1000)["segments"]
        assert len(part["points"]) == 3

    def test_a_large_window_keeps_the_extremes_of_each_bucket(self, run_dir: Path) -> None:
        record(run_dir, 10_000)
        (part,) = window(run_dir, "daq.0", 0, 100, 100)["segments"]
        assert part["kind"] == "envelope"
        assert len(part["points"]) == 50
        assert part["points"][0][1] == 0.0
        assert part["points"][-1][3] == 9999.0
        # [t, low1, low2, high1, high2]: the second channel is the first's negative.
        assert part["points"][0][2] == -part["points"][0][3]

    def test_a_spike_survives_being_thinned(self, run_dir: Path) -> None:
        recorder = Recorder(run_dir, "daq.0")
        batch = scans(5000)
        batch[2500] = (2501, 0.0, T0 + 25.0, [99999.0, 0.0])
        recorder.write(TWO, 100.0, batch)
        recorder.close()
        (part,) = window(run_dir, "daq.0", 0, 100, 100)["segments"]
        assert max(point[3] for point in part["points"]) == 99999.0

    def test_a_window_with_no_scans_is_empty(self, run_dir: Path) -> None:
        record(run_dir)
        assert window(run_dir, "daq.0", 50, 60, 100)["segments"] == []

    def test_an_instrument_that_recorded_nothing_has_no_window(self, run_dir: Path) -> None:
        record(run_dir)
        assert window(run_dir, "daq.9", 0, 1, 100) is None

    def test_times_are_seconds_from_the_first_scan_of_any_instrument(self, run_dir: Path) -> None:
        recorder = Recorder(run_dir, "daq.0")
        recorder.write(TWO, 100.0, scans(10, start=T0))
        recorder.close()
        late = Recorder(run_dir, "daq.1")
        late.write(TWO, 100.0, scans(10, start=T0 + 5))
        late.close()
        (part,) = window(run_dir, "daq.1", 0, 100, 100)["segments"]
        assert part["points"][0][0] == pytest.approx(5.0)
        assert recordings(run_dir)["origin"] == pytest.approx(T0)

    def test_the_number_of_points_is_held_to_a_sensible_range(self, run_dir: Path) -> None:
        record(run_dir, 1000)
        (part,) = window(run_dir, "daq.0", 0, 100, 1)["segments"]
        assert len(part["points"]) == 25

    def test_a_scan_half_written_is_not_counted(self, run_dir: Path) -> None:
        record(run_dir, 10)
        with (run_dir / "daq" / "daq.0.001.v").open("ab") as handle:
            handle.write(b"\x00\x00\x80")
        assert recordings(run_dir)["instruments"][0]["rows"] == 10
