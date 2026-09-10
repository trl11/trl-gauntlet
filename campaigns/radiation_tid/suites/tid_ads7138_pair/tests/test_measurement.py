"""The paired measurement: the harness maths, the checks, and what fails them."""

from __future__ import annotations

from typing import Any

import pytest
from suite.adc import GPO_VALUE, STATUS_ALIVE, STATUS_FAULTS, MockAdc, MockBench
from suite.part import Part
from suite.profile import OVERSAMPLING, TidAds7138PairProfile
from suite.runner import across, invert, levels, link, named_bits, oversampling_spreads, pattern_for, quiet_rail

# The harness on this bench: straight through but for channels 2 and 3, which
# cross. Measured by driving a walking one from each part in turn.
BENCH_MAP = [0, 1, 3, 2, 4, 5, 6, 7]


class _StuckLow:
    """A part with one output that will not leave the low rail, whatever it is told."""

    def __init__(self, inner: Any, *, channel: int) -> None:
        self._inner = inner
        self._stuck = 1 << channel

    def read_data(self) -> int:
        return int(self._inner.read_data())

    def read_register(self, register: int) -> int:
        return int(self._inner.read_register(register))

    def write_register(self, register: int, value: int) -> None:
        if register == GPO_VALUE:
            value &= ~self._stuck
        self._inner.write_register(register, value)


def _pair(channel_map: list[int] | None = None) -> tuple[Part, Part]:
    """A part in the beam and a reference, on a simulated harness."""
    bench = MockBench(channel_map or BENCH_MAP)
    dut = Part(MockAdc(bench, "dut"), "the part in the beam", settle_s=0)
    ref = Part(MockAdc(bench, "ref"), "the reference", settle_s=0)
    dut.configure()
    ref.configure()
    return dut, ref


class TestChannelMap:
    def test_a_straight_harness_changes_nothing(self) -> None:
        assert across(0xAA, list(range(8))) == 0xAA

    @pytest.mark.parametrize(("driven", "seen"), [(0xAA, 0xA6), (0x55, 0x59), (0x01, 0x01), (0x80, 0x80)])
    def test_the_bench_harness_matches_what_it_measured(self, driven: int, seen: int) -> None:
        """Captured by driving each channel in turn and reading the far end."""
        assert across(driven, BENCH_MAP) == seen

    def test_reading_the_harness_from_the_other_end_undoes_it(self) -> None:
        assert across(across(0xAA, BENCH_MAP), invert(BENCH_MAP)) == 0xAA

    def test_a_difference_is_named_at_the_driving_end(self) -> None:
        # Channel 3 of the driving part arrives on channel 2 of the other, so
        # a difference in bit 2 is that part's channel 3.
        assert named_bits(1 << 2, BENCH_MAP) == "CH3"


class TestPatterns:
    def test_both_rails_and_both_alternations_come_first(self) -> None:
        assert [pattern_for(i) for i in range(4)] == [0x00, 0xFF, 0xAA, 0x55]

    def test_every_channel_is_walked_high_and_low(self) -> None:
        seen = {pattern_for(i) for i in range(20)}
        assert all(1 << bit in seen for bit in range(8))
        assert all(0xFF ^ (1 << bit) in seen for bit in range(8))

    def test_the_patterns_cycle(self) -> None:
        assert pattern_for(0) == pattern_for(20)


class TestLink:
    def test_a_pattern_crosses_the_harness_intact(self) -> None:
        dut, ref = _pair()
        seen, complaint = link(dut, ref, 0xAA, BENCH_MAP)
        assert complaint == ""
        assert seen == 0xA6

    def test_it_crosses_back_the_other_way(self) -> None:
        dut, ref = _pair()
        seen, complaint = link(ref, dut, 0xAA, invert(BENCH_MAP))
        assert complaint == ""
        assert seen == 0xA6

    def test_a_harness_wired_otherwise_than_declared_is_a_fault(self) -> None:
        """The map is bench wiring, so a wrong one has to be reported, not absorbed."""
        dut, ref = _pair(channel_map=BENCH_MAP)
        _, complaint = link(dut, ref, 0xAA, list(range(8)))
        assert "and not" in complaint

    def test_an_output_stuck_low_names_its_channel(self) -> None:
        bench = MockBench(BENCH_MAP)
        dut = Part(_StuckLow(MockAdc(bench, "dut"), channel=5), "the part in the beam", settle_s=0)
        ref = Part(MockAdc(bench, "ref"), "the reference", settle_s=0)
        dut.configure()
        ref.configure()
        _, complaint = link(dut, ref, 0xFF, BENCH_MAP)
        assert "CH5" in complaint


class TestLevels:
    def test_a_driven_wire_reads_at_the_rails(self) -> None:
        dut, ref = _pair()
        low, high = levels(dut, ref, BENCH_MAP)
        assert all(value < 100 for value in low)
        assert all(value > 3000 for value in high)

    def test_every_channel_is_measured_at_the_driving_end(self) -> None:
        dut, ref = _pair()
        low, high = levels(dut, ref, BENCH_MAP)
        assert len(low) == 8
        assert len(high) == 8


class TestOversampling:
    def test_averaging_more_conversions_quietens_the_part(self) -> None:
        dut, ref = _pair()
        ref.drive(0xFF)
        dut.listen_analog()
        spreads = oversampling_spreads(dut, 0, 64)
        assert spreads[-1] < spreads[0]

    def test_the_sweep_visits_every_setting_it_declares(self) -> None:
        dut, ref = _pair()
        ref.drive(0xFF)
        dut.listen_analog()
        assert len(oversampling_spreads(dut, 0, 8)) == len(OVERSAMPLING)


class TestQuietRail:
    def test_the_rail_with_the_most_room_inside_the_scale_is_chosen(self) -> None:
        """Neither end of the scale shows a spread, so the check must avoid them."""
        dut, ref = _pair()
        rail = quiet_rail(dut, ref, 0)
        assert rail in (0x00, 0xFF)
        ref.drive(rail)
        dut.listen_analog()
        assert not dut.saturated(0)


class TestHealth:
    def test_a_configured_part_reports_itself_alive_and_faultless(self) -> None:
        dut, _ = _pair()
        assert dut.status() & STATUS_ALIVE
        assert not dut.status() & STATUS_FAULTS

    def test_the_baseline_registers_read_back_as_written(self) -> None:
        dut, _ = _pair()
        assert dut.baseline_drift() == []

    def test_calibration_finishes(self) -> None:
        dut, _ = _pair()
        assert dut.calibrate() < 500.0


class TestProfile:
    def test_a_channel_map_naming_one_channel_twice_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="more than once"):
            TidAds7138PairProfile(channel_map=[0, 0, 2, 3, 4, 5, 6, 7])

    def test_a_channel_outside_the_part_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not one of the part's eight"):
            TidAds7138PairProfile(channel_map=[0, 1, 2, 3, 4, 5, 6, 8])

    def test_thresholds_that_no_level_could_pass_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="above vol_max_mv"):
            TidAds7138PairProfile(vol_max_mv=3000.0, voh_min_mv=2000.0)

    def test_the_bench_harness_is_the_default(self) -> None:
        assert TidAds7138PairProfile().channel_map == BENCH_MAP
