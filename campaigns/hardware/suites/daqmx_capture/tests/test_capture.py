"""What the profile refuses, and what a capture is judged against."""

from __future__ import annotations

from pathlib import Path

import pytest
from gauntlet_sdk import IterationOutcome
from pydantic import ValidationError
from suite.profile import Channel, DaqmxCaptureProfile, metric_key
from suite.runner import _evaluate, _iterate


def _judged(profile: DaqmxCaptureProfile, **readings: float | None) -> IterationOutcome:
    """One iteration whose success is taken over the profile's windows."""
    outcome = sample(**readings)
    faults = [
        fault
        for channel in profile.channels
        if isinstance(value := outcome.metrics["daq"].get(channel.key), (int, float))
        and (fault := channel.fault(value))
    ]
    if faults:
        return IterationOutcome(
            success=False,
            reason="; ".join(faults),
            metrics=outcome.metrics,
            phase_records=[],
            summary="",
        )
    return outcome


def sample(**readings: float | None) -> IterationOutcome:
    """One iteration that recorded these channels, by metric name."""
    values = {key: value for key, value in readings.items() if value is not None}
    return IterationOutcome(
        success=len(values) == len(readings),
        reason="",
        metrics={"daq": values},
        phase_records=[],
        summary="",
    )


class TestProfile:
    def test_a_label_becomes_the_metric_name(self) -> None:
        assert metric_key("Rail 3V3", "ai0") == "rail_3v3"

    def test_a_label_that_slugs_to_nothing_falls_back_to_the_channel(self) -> None:
        assert metric_key("!!", "ai2") == "ai2"

    def test_a_channel_is_named_the_way_ni_names_one(self) -> None:
        with pytest.raises(ValidationError):
            Channel(channel="1")

    def test_the_range_is_left_alone_when_the_profile_names_none(self) -> None:
        assert Channel(channel="ai0").mode == ""

    def test_a_range_that_is_not_one_word_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="one word"):
            Channel(channel="ai0", mode="500 mv")

    def test_a_channel_listed_twice_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="listed more than once"):
            DaqmxCaptureProfile(channels=[Channel(channel="ai0"), Channel(channel="ai0")])

    def test_two_labels_folding_to_one_metric_name_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="same metric name"):
            DaqmxCaptureProfile(
                channels=[Channel(channel="ai0", label="Rail 3V3"), Channel(channel="ai1", label="rail 3v3")]
            )


class TestRunLabels:
    def test_a_label_renames_a_channel_the_profile_lists(self) -> None:
        profile = DaqmxCaptureProfile(channels=[Channel(channel="ai0", label="AI 0")], labels="ai0=Rail 3V3")
        assert [(c.channel, c.label, c.key) for c in profile.channels] == [("ai0", "Rail 3V3", "rail_3v3")]

    def test_a_label_for_a_channel_not_listed_adds_it(self) -> None:
        profile = DaqmxCaptureProfile(channels=[Channel(channel="ai0")], labels="ai2=Ground")
        assert [(c.channel, c.label) for c in profile.channels] == [("ai0", ""), ("ai2", "Ground")]

    def test_several_are_taken_in_one_go(self) -> None:
        profile = DaqmxCaptureProfile(channels=[Channel(channel="ai0")], labels="ai0=Rail 3V3, ai1=Shunt")
        assert [c.label for c in profile.channels] == ["Rail 3V3", "Shunt"]

    def test_something_that_is_not_a_pair_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="is not a channel and a label"):
            DaqmxCaptureProfile(labels="Rail 3V3")

    def test_a_channel_ni_would_not_name_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="is not a channel"):
            DaqmxCaptureProfile(labels="3=Rail 3V3")

    def test_two_labels_folding_to_one_metric_name_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="same metric name"):
            DaqmxCaptureProfile(channels=[Channel(channel="ai0", label="Rail 3V3")], labels="ai1=rail 3v3")


class TestLimits:
    def test_a_reading_inside_its_window_is_no_fault(self) -> None:
        assert Channel(channel="ai0", min_v=3.0, max_v=3.6).fault(3.3) == ""

    def test_a_reading_below_the_floor_says_so(self) -> None:
        fault = Channel(channel="ai0", label="Rail 3V3", min_v=3.0).fault(0.62)
        assert fault == "Rail 3V3 read 0.62V, below 3V"

    def test_a_reading_above_the_ceiling_says_so(self) -> None:
        fault = Channel(channel="ai0", label="Rail 3V3", max_v=3.6).fault(4.1)
        assert fault == "Rail 3V3 read 4.1V, above 3.6V"

    def test_a_channel_with_no_window_faults_on_nothing(self) -> None:
        assert Channel(channel="ai0").fault(-99.0) == ""

    def test_a_window_with_nothing_inside_it_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="must be below max_v"):
            Channel(channel="ai0", min_v=3.6, max_v=3.0)


class _Ctx:
    """The slice of the suite context the capture path touches."""

    def __init__(self, profile: DaqmxCaptureProfile, run_dir: Path, daq: object) -> None:
        self.profile = profile
        self.run_dir = run_dir
        self.extras: dict[str, object] = {"daq": daq}

    def artifact(self, *parts: str) -> Path:
        path = self.run_dir.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


class _Ictx:
    def __init__(self, iteration: int = 1) -> None:
        self.iteration = iteration
        self.elapsed_run_s = 0.0


class _CapturingDaq:
    """A module that answers a capture with the samples the test gave it."""

    def __init__(self, samples: dict[str, list[float]], rate_hz: float = 25000.0) -> None:
        self.asked: list[tuple[float, int]] = []
        self._rate_hz = rate_hz
        self._samples = samples

    def sample(self) -> dict[str, dict[str, object]]:
        raise AssertionError("a capturing run must not fall back to the mean")

    def capture(self, rate_hz: float, samples: int) -> dict[str, object]:
        self.asked.append((rate_hz, samples))
        return {
            "rate_hz": self._rate_hz,
            "samples": samples,
            "channels": {
                name: {
                    "label": name.upper(),
                    "mean": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values),
                    "peak_to_peak": max(values) - min(values),
                    "values": values,
                }
                for name, values in self._samples.items()
            },
        }


def _captured(tmp_path: Path, profile: DaqmxCaptureProfile, daq: _CapturingDaq) -> IterationOutcome:
    """One iteration of a capturing run, against a module the test controls."""
    return _iterate(_Ctx(profile, tmp_path, daq), _Ictx())  # type: ignore[arg-type]


class TestWaveformCapture:
    def profile(self, **extra: object) -> DaqmxCaptureProfile:
        return DaqmxCaptureProfile(
            channels=[Channel(channel="ai0", label="Rail")],
            capture_rate_hz=25000.0,
            capture_samples=4,
            **extra,
        )

    def test_the_rate_and_the_depth_asked_for_are_the_profile_s(self, tmp_path: Path) -> None:
        daq = _CapturingDaq({"ai0": [0.001, 0.002, 0.0025, 0.002]})
        _captured(tmp_path, self.profile(), daq)
        assert daq.asked == [(25000.0, 4)]

    def test_the_samples_are_written_as_csv_beside_the_run(self, tmp_path: Path) -> None:
        daq = _CapturingDaq({"ai0": [0.001, 0.002, 0.0025, 0.002]})
        _captured(tmp_path, self.profile(), daq)

        rows = (tmp_path / "captures" / "capture_0001.csv").read_text().splitlines()
        assert rows[0] == "t_s,rail"
        assert rows[1] == "0,0.001"
        # The second sample sits one period into the window.
        assert rows[2].startswith("4e-05,")
        assert len(rows) == 5

    def test_the_peak_to_peak_of_the_window_is_recorded(self, tmp_path: Path) -> None:
        daq = _CapturingDaq({"ai0": [0.001, 0.002, 0.0025, 0.002]})
        outcome = _captured(tmp_path, self.profile(), daq)
        assert outcome.metrics["daq"]["rail_pp"] == pytest.approx(0.0015)

    def test_a_peak_outside_the_window_fails_the_sample(self, tmp_path: Path) -> None:
        # The mean sits inside the window and the signal does not, which is
        # the whole reason a capture is judged over its extremes.
        profile = DaqmxCaptureProfile(
            channels=[Channel(channel="ai0", label="Rail", min_v=0.0, max_v=0.003)],
            capture_rate_hz=25000.0,
            capture_samples=4,
        )
        daq = _CapturingDaq({"ai0": [0.001, 0.009, 0.0011, 0.001]})
        outcome = _captured(tmp_path, profile, daq)
        assert outcome.success is False
        assert outcome.reason == "Rail read 0.009V, above 0.003V"

    def test_a_trough_outside_the_window_fails_the_sample(self, tmp_path: Path) -> None:
        profile = DaqmxCaptureProfile(
            channels=[Channel(channel="ai0", label="Rail", min_v=0.0, max_v=0.003)],
            capture_rate_hz=25000.0,
            capture_samples=4,
        )
        daq = _CapturingDaq({"ai0": [0.001, 0.002, -0.004, 0.001]})
        outcome = _captured(tmp_path, profile, daq)
        assert outcome.success is False
        assert outcome.reason == "Rail read -0.004V, below 0V"

    def test_a_signal_that_stays_inside_the_window_passes(self, tmp_path: Path) -> None:
        profile = DaqmxCaptureProfile(
            channels=[Channel(channel="ai0", label="Rail", min_v=0.0, max_v=0.003)],
            capture_rate_hz=25000.0,
            capture_samples=4,
        )
        daq = _CapturingDaq({"ai0": [0.001, 0.002, 0.0025, 0.002]})
        assert _captured(tmp_path, profile, daq).success is True


class TestVerdict:
    def profile(self, **limits: float) -> DaqmxCaptureProfile:
        return DaqmxCaptureProfile(
            channels=[
                Channel(channel="ai0", label="Rail", **limits),
                Channel(channel="ai1", label="Shunt"),
            ]
        )

    def test_a_capture_that_read_both_channels_passes(self) -> None:
        outcomes = [sample(rail=0.5, shunt=0.1), sample(rail=0.51, shunt=0.11)]
        assert _evaluate(outcomes, self.profile()) == (True, "")

    def test_a_capture_with_no_samples_fails(self) -> None:
        assert _evaluate([], self.profile()) == (False, "no samples collected")

    def test_a_missed_reading_fails_a_profile_that_allows_none(self) -> None:
        outcomes = [sample(rail=0.5, shunt=0.1), sample(rail=0.51, shunt=None)]
        passed, reason = _evaluate(outcomes, self.profile())
        assert passed is False
        assert "not usable" in reason

    def test_a_reading_outside_its_window_fails_the_run(self) -> None:
        profile = self.profile(min_v=3.0, max_v=3.6)
        outcomes = [
            _judged(profile, rail=3.3, shunt=0.1),
            _judged(profile, rail=0.62, shunt=0.11),
        ]
        passed, reason = _evaluate(outcomes, profile)
        assert passed is False
        assert "Rail read 0.62V, below 3V" in reason

    def test_readings_inside_their_window_pass(self) -> None:
        profile = self.profile(min_v=3.0, max_v=3.6)
        outcomes = [_judged(profile, rail=3.3, shunt=0.1), _judged(profile, rail=3.31, shunt=0.11)]
        assert _evaluate(outcomes, profile) == (True, "")

    def test_a_channel_that_never_read_at_all_is_named(self) -> None:
        profile = self.profile()
        profile.max_missed_samples = 2
        outcomes = [sample(rail=0.5, shunt=None), sample(rail=0.51, shunt=None)]
        passed, reason = _evaluate(outcomes, profile)
        assert passed is False
        assert "no reading at all from shunt" in reason

    def test_a_reading_that_never_moves_is_an_unwired_input(self) -> None:
        # A 24-bit converter carries its own noise, so a series identical to
        # the microvolt is an input pinned at the rail rather than a quiet one.
        outcomes = [sample(rail=0.5, shunt=0.624114), sample(rail=0.51, shunt=0.624114)]
        passed, reason = _evaluate(outcomes, self.profile())
        assert passed is False
        assert "shunt never changed" in reason

    def test_one_sample_is_not_called_unwired(self) -> None:
        # There is nothing to compare it with, so a single reading says only
        # that the channel answered.
        assert _evaluate([sample(rail=0.5, shunt=0.624114)], self.profile()) == (True, "")
