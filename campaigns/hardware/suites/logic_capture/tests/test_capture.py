"""What the profile refuses, and what a capture is judged against."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from suite import pattern
from suite.profile import Channel, LogicCaptureProfile, metric_key
from suite.runner import _fault


class TestProfile:
    def test_a_label_becomes_the_metric_name(self) -> None:
        assert metric_key("Rail 3V3", "1") == "rail_3v3"

    def test_a_label_that_slugs_to_nothing_falls_back_to_the_channel(self) -> None:
        assert metric_key("!!", "4") == "ch4"

    def test_an_unknown_rate_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="rate must be one of"):
            LogicCaptureProfile(rate="99mhz")

    def test_an_unknown_window_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="window must be one of"):
            LogicCaptureProfile(window="1s")

    def test_an_unknown_expectation_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="expect must be one of"):
            Channel(channel="1", expect="wiggling")

    def test_a_channel_listed_twice_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="listed more than once"):
            LogicCaptureProfile(channels=[Channel(channel="1"), Channel(channel="1")])

    def test_two_labels_folding_to_one_metric_name_are_refused(self) -> None:
        # One series would silently overwrite the other.
        with pytest.raises(ValidationError, match="same metric name"):
            LogicCaptureProfile(channels=[Channel(channel="1", label="SCL"), Channel(channel="2", label="scl")])


class TestExpectations:
    def test_a_probe_asked_nothing_never_fails(self) -> None:
        channel = Channel(channel="1", expect="any")
        assert _fault(channel, {"edges": 0, "level": 0}) == ""

    def test_an_active_probe_that_stopped_moving_fails(self) -> None:
        channel = Channel(channel="1", label="SCL", expect="active")
        assert _fault(channel, {"edges": 0, "level": 1}) == "scl did not move"
        assert _fault(channel, {"edges": 40, "level": 1}) == ""

    def test_a_level_that_is_not_what_it_should_be_fails(self) -> None:
        high = Channel(channel="2", label="RESET", expect="high")
        assert _fault(high, {"edges": 0, "level": 0}) == "reset is not high"
        assert _fault(high, {"edges": 0, "level": 1}) == ""
        low = Channel(channel="3", label="EN", expect="low")
        assert _fault(low, {"edges": 0, "level": 1}) == "en is not low"


class TestPattern:
    def test_a_capture_is_drawn_as_a_png_of_every_channel(self) -> None:
        image, channels = pattern.synthesise(1_000_000, 0.01, 0.0)
        assert image.startswith(b"\x89PNG\r\n\x1a\n")
        assert sorted(channels) == [str(number) for number in range(1, 9)]

    def test_every_channel_halves_the_one_above_it(self) -> None:
        _image, channels = pattern.synthesise(1_000_000, 0.01, 0.0)
        assert channels["2"]["frequency"] == channels["1"]["frequency"] / 2

    def test_a_column_holding_both_levels_is_drawn_as_both(self) -> None:
        # A wave far too fast to resolve at this width is a band rather than
        # whichever level happened to land on each column.
        drawn = pattern.columns(0, 24_000_000, 0.1, 0.0)
        assert all(mixed for _level, mixed in drawn)

    def test_a_wave_slow_enough_to_draw_is_drawn_as_a_square_wave(self) -> None:
        drawn = pattern.columns(7, 20_000, 0.1, 0.0)
        assert not all(mixed for _level, mixed in drawn)
        assert {level for level, _mixed in drawn} == {0, 1}
