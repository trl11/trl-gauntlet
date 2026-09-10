"""What the profile refuses, and what a capture is judged against."""

from __future__ import annotations

import pytest
from gauntlet_sdk import IterationOutcome
from pydantic import ValidationError
from suite.profile import Channel, DaqmxCaptureProfile, metric_key
from suite.runner import _evaluate


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


class TestVerdict:
    def profile(self) -> DaqmxCaptureProfile:
        return DaqmxCaptureProfile(
            channels=[Channel(channel="ai0", label="Rail"), Channel(channel="ai1", label="Shunt")]
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
        assert "missed a reading" in reason

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
