"""What the profile refuses, and what one sample is judged against."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from suite.adc import GPI_VALUE, MockAdc
from suite.analyzer import MockAnalyzer, levels_and_edges
from suite.profile import TidAds7138Profile
from suite.runner import (
    _CONFIGURATION,
    INPUT_HIGH,
    INPUT_LOW,
    OUTPUT_HIGH,
    OUTPUT_LOW,
    PULSE,
    TOGGLE,
    analog_faults,
    driven_for,
    input_faults,
    pin_faults,
    volts,
)

# GPIO2 tied high; GPIO5 tied low reads as nothing.
HIGH = 1 << INPUT_HIGH


class TestProfile:
    def test_two_outputs_on_one_probe_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="same probe"):
            TidAds7138Profile(low_probe=3, high_probe=3)

    def test_a_probe_the_analyzer_does_not_have_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="analyzer's eight"):
            TidAds7138Profile(pulse_probe=9)

    def test_an_analog_window_upside_down_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="below its maximum"):
            TidAds7138Profile(ain1_min_v=2.0, ain1_max_v=1.0)


class TestDrive:
    def test_the_low_output_is_low_and_the_high_output_is_high(self) -> None:
        for iteration in range(4):
            driven = driven_for(iteration)
            assert not driven & (1 << OUTPUT_LOW)
            assert driven & (1 << OUTPUT_HIGH)

    def test_the_pulse_flips_every_sample(self) -> None:
        assert [(driven_for(i) >> PULSE) & 1 for i in range(4)] == [0, 1, 0, 1]

    def test_the_toggled_pin_flips_opposite_the_pulse(self) -> None:
        assert [(driven_for(i) >> TOGGLE) & 1 for i in range(4)] == [1, 0, 1, 0]


class TestInputs:
    def test_inputs_as_wired_have_no_fault(self) -> None:
        assert input_faults(HIGH | driven_for(1), driven_for(1)) == []

    def test_a_tied_high_input_reading_low_is_named(self) -> None:
        assert input_faults(driven_for(0), driven_for(0)) == ["GPIO2 (pin 1) read 0, not 1"]

    def test_a_tied_low_input_reading_high_is_named(self) -> None:
        driven = driven_for(0)
        assert input_faults(HIGH | (1 << INPUT_LOW) | driven, driven) == ["GPIO5 (pin 4) read 1, not 0"]

    def test_a_toggled_pin_that_did_not_follow_in_the_part_is_named(self) -> None:
        driven = driven_for(0)
        (fault,) = input_faults(HIGH | (driven & ~(1 << TOGGLE)), driven)
        assert fault == "the part read its outputs as 0x40, not 0x48"


class TestPins:
    def test_outputs_seen_as_driven_have_no_fault(self) -> None:
        profile = TidAds7138Profile()
        captured = {2: (0, 0), 3: (0, 0), 5: (1, 0), 6: (1, 0)}
        assert pin_faults(captured, profile, driven_for(1)) == []

    def test_a_pulse_that_did_not_follow_is_named(self) -> None:
        profile = TidAds7138Profile()
        captured = {2: (0, 0), 3: (0, 0), 5: (1, 0), 6: (0, 0)}
        assert pin_faults(captured, profile, driven_for(1)) == ["GPIO7 (pin 6) sat at 0 on probe 6, not 1"]

    def test_a_toggled_pin_that_did_not_follow_on_the_probe_is_named(self) -> None:
        profile = TidAds7138Profile()
        captured = {2: (0, 0), 3: (0, 0), 5: (1, 0), 6: (0, 0)}
        assert pin_faults(captured, profile, driven_for(0)) == ["GPIO3 (pin 2) sat at 0 on probe 2, not 1"]

    def test_a_glitch_on_a_held_output_is_named(self) -> None:
        profile = TidAds7138Profile()
        captured = {2: (1, 0), 3: (0, 2), 5: (1, 0), 6: (0, 0)}
        assert pin_faults(captured, profile, driven_for(0)) == [
            "GPIO4 (pin 3) changed 2 times on probe 3 while held at 0"
        ]

    def test_the_capture_is_read_by_probe_number(self) -> None:
        assert levels_and_edges({"3": {"level": 1, "edges": 4}, "5": {"level": None}}) == {3: (1, 4), 5: (0, 0)}


class TestAnalog:
    def test_a_code_converts_against_avdd(self) -> None:
        assert volts(2048, 3.3) == pytest.approx(1.65)

    def test_a_reading_outside_its_window_is_named(self) -> None:
        profile = TidAds7138Profile()
        (fault,) = analog_faults({0: 1.85, 1: 2.5}, profile)
        assert fault.startswith("AIN1 (pin 16) read 2.5000 V")


class TestMock:
    def test_the_mock_part_on_its_board_passes_every_check(self) -> None:
        profile = TidAds7138Profile()
        adc = MockAdc(high=INPUT_HIGH, low=INPUT_LOW, code=2296)
        for register, value in _CONFIGURATION.items():
            adc.write_register(register, value)
        analyzer = MockAnalyzer(adc, {TOGGLE: 2, OUTPUT_LOW: 3, OUTPUT_HIGH: 5, PULSE: 6})
        for iteration in range(2):
            driven = driven_for(iteration)
            adc.write_register(0x0B, driven)
            assert input_faults(adc.read_register(GPI_VALUE), driven) == []
            assert pin_faults(analyzer.capture("1mhz", "10ms"), profile, driven) == []
            assert analog_faults({0: volts(adc.convert(0), 3.3), 1: volts(adc.convert(1), 3.3)}, profile) == []
