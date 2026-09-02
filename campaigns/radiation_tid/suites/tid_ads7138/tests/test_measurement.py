"""What the profile refuses, and what one pattern is judged against."""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError
from suite.adc import GPI_VALUE, GPIO_CFG, GPO_VALUE, PIN_CFG, MockAdc
from suite.analyzer import MockAnalyzer
from suite.profile import TidAds7138Profile
from suite.runner import _PATTERNS, channel_labels, named_bits, pattern_for, probes_to_byte

# The wiring of the bench this suite was written against: the probe each
# output is clipped to, in the scrambled order the ribbon gives.
BENCH_MAP = [5, 3, 1, 7, 8, 6, 4, 2]


class TestProfile:
    def test_an_unknown_rate_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="rate must be one of"):
            TidAds7138Profile(rate="99mhz")

    def test_an_unknown_window_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="window must be one of"):
            TidAds7138Profile(window="1s")

    def test_a_probe_named_twice_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="more than once"):
            TidAds7138Profile(probe_map=[1, 1, 2, 3, 4, 5, 6, 7])

    def test_a_probe_the_analyzer_does_not_have_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not one of the analyzer's eight"):
            TidAds7138Profile(probe_map=[1, 2, 3, 4, 5, 6, 7, 9])

    def test_an_address_outside_the_bus_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            TidAds7138Profile(address=0x80)


class TestPatterns:
    def test_both_rails_and_both_alternations_are_driven(self) -> None:
        assert {0x00, 0xFF, 0xAA, 0x55} <= set(_PATTERNS)

    def test_every_output_is_driven_high_and_low_on_its_own(self) -> None:
        for bit in range(8):
            assert 1 << bit in _PATTERNS
            assert 0xFF ^ (1 << bit) in _PATTERNS

    def test_the_patterns_cycle(self) -> None:
        assert pattern_for(0) == pattern_for(len(_PATTERNS))


class TestProbes:
    def test_the_scrambled_probes_read_back_as_the_driven_byte(self) -> None:
        levels = {5: 1, 3: 0, 1: 0, 7: 1, 8: 0, 6: 0, 4: 0, 2: 0}
        assert probes_to_byte(levels, BENCH_MAP) == 0b00001001

    def test_a_probe_the_capture_missed_reads_low(self) -> None:
        assert probes_to_byte({}, BENCH_MAP) == 0x00

    def test_a_difference_names_the_outputs_it_covers(self) -> None:
        assert named_bits(0b00100100) == "GPO2, GPO5"


class TestChannelLabels:
    def test_each_probe_is_labelled_with_the_output_clipped_to_it(self) -> None:
        # GPO0 is on probe 5, so probe 5's lane is the one that reads GPO0.
        assert channel_labels(BENCH_MAP)[4] == "GPO0"
        assert channel_labels(BENCH_MAP)[0] == "GPO2"

    def test_every_probe_is_named_once(self) -> None:
        assert sorted(channel_labels(BENCH_MAP)) == [f"GPO{bit}" for bit in range(8)]


class TestMockPart:
    def test_the_inputs_mirror_what_the_outputs_are_driving(self) -> None:
        adc = MockAdc()
        adc.write_register(PIN_CFG, 0xFF)
        adc.write_register(GPIO_CFG, 0xFF)
        adc.write_register(GPO_VALUE, 0xA5)
        assert adc.read_register(GPI_VALUE) == 0xA5

    def test_a_channel_that_is_not_an_output_reads_low(self) -> None:
        adc = MockAdc()
        adc.write_register(PIN_CFG, 0xFF)
        adc.write_register(GPIO_CFG, 0x0F)
        adc.write_register(GPO_VALUE, 0xFF)
        assert adc.read_register(GPI_VALUE) == 0x0F

    def test_the_trace_holds_the_pattern_the_outputs_were_driving(self) -> None:
        adc = MockAdc()
        adc.write_register(PIN_CFG, 0xFF)
        adc.write_register(GPIO_CFG, 0xFF)
        adc.write_register(GPO_VALUE, 0xA5)
        captured = MockAnalyzer(adc, BENCH_MAP).capture("1mhz", "1ms")
        samples = base64.b64decode(captured.samples_base64)
        assert len(samples) == captured.samples
        # A sample byte carries probe n in bit n-1, which is the shape the
        # viewer decodes and the opposite end of the same wiring.
        levels = {probe: (samples[0] >> (probe - 1)) & 1 for probe in range(1, 9)}
        assert probes_to_byte(levels, BENCH_MAP) == 0xA5

    def test_the_analyzer_reports_the_pattern_through_the_probe_map(self) -> None:
        adc = MockAdc()
        adc.write_register(PIN_CFG, 0xFF)
        adc.write_register(GPIO_CFG, 0xFF)
        adc.write_register(GPO_VALUE, 0x81)
        captured = MockAnalyzer(adc, BENCH_MAP).capture("1mhz", "1ms")
        assert probes_to_byte(captured.levels(), BENCH_MAP) == 0x81
