"""What the profile refuses, and what one sample is judged against."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from suite.adc import GPI_VALUE, MockAdc
from suite.analyzer import MockAnalyzer, levels_and_edges
from suite.profile import TidAds7138Profile
from suite.runner import (
    _CONFIGURATION,
    CLOCK,
    INPUT_HIGH,
    INPUT_LOW,
    OUTPUT_HIGH,
    OUTPUT_LOW,
    PULSE,
    analog_faults,
    driven_for,
    input_faults,
    pin_faults,
    volts,
)

# GPIO2 high, GPIO5 low, the clock at each level, and the outputs as driven.
HIGH = 1 << INPUT_HIGH
TICK = 1 << CLOCK


def _reads(driven: int) -> list[int]:
    return [HIGH | driven, HIGH | TICK | driven]


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


class TestInputs:
    def test_inputs_as_wired_have_no_fault(self) -> None:
        assert input_faults(_reads(driven_for(1)), driven_for(1)) == []

    def test_a_stopped_clock_is_named(self) -> None:
        driven = driven_for(0)
        (fault,) = input_faults([HIGH | TICK | driven] * 4, driven)
        assert "GPIO3 (pin 2) held 1" in fault

    def test_a_tied_high_input_reading_low_is_named(self) -> None:
        driven = driven_for(0)
        reads = [read & ~HIGH for read in _reads(driven)]
        assert input_faults(reads, driven) == ["GPIO2 (pin 1) read 0, not 1"]

    def test_a_tied_low_input_reading_high_is_named(self) -> None:
        driven = driven_for(0)
        reads = [read | (1 << INPUT_LOW) for read in _reads(driven)]
        assert input_faults(reads, driven) == ["GPIO5 (pin 4) read 1, not 0"]


class TestPins:
    def test_outputs_seen_as_driven_have_no_fault(self) -> None:
        profile = TidAds7138Profile()
        captured = {3: (0, 0), 5: (1, 0), 6: (1, 0)}
        assert pin_faults(captured, profile, driven_for(1)) == []

    def test_a_pulse_that_did_not_follow_is_named(self) -> None:
        profile = TidAds7138Profile()
        captured = {3: (0, 0), 5: (1, 0), 6: (0, 0)}
        assert pin_faults(captured, profile, driven_for(1)) == ["GPIO7 (pin 6) sat at 0 on probe 6, not 1"]

    def test_a_glitch_on_a_held_output_is_named(self) -> None:
        profile = TidAds7138Profile()
        captured = {3: (0, 2), 5: (1, 0), 6: (0, 0)}
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
        adc = MockAdc(high=INPUT_HIGH, low=INPUT_LOW, clock=CLOCK, code=2296)
        for register, value in _CONFIGURATION.items():
            adc.write_register(register, value)
        analyzer = MockAnalyzer(adc, {OUTPUT_LOW: 3, OUTPUT_HIGH: 5, PULSE: 6})
        for iteration in range(2):
            driven = driven_for(iteration)
            adc.write_register(0x0B, driven)
            reads = [adc.read_register(GPI_VALUE) for _ in range(4)]
            assert input_faults(reads, driven) == []
            assert pin_faults(analyzer.capture("1mhz", "10ms"), profile, driven) == []
            assert analog_faults({0: volts(adc.convert(0), 3.3), 1: volts(adc.convert(1), 3.3)}, profile) == []
