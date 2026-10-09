"""What the profile refuses, how a reading converts, and what a register is judged against."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from suite.profile import TidTemperatureSensorProfile
from suite.runner import register_faults, written
from suite.sensor import CONFIGURATION, PARTS, T_HIGH, MockSensor, Part, celsius


class TestProfile:
    def test_an_address_the_straps_cannot_select_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            TidTemperatureSensorProfile(address=0x10)

    def test_an_address_only_the_tmp100_answers_is_refused_for_the_tmp112(self) -> None:
        TidTemperatureSensorProfile(part="tmp100", address=0x4C)
        with pytest.raises(ValidationError, match="TMP112 cannot be strapped to 0x4c"):
            TidTemperatureSensorProfile(part="tmp112", address=0x4C)

    def test_bounds_out_of_order_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="min_c must be below max_c"):
            TidTemperatureSensorProfile(min_c=40.0, max_c=10.0)


class TestCelsius:
    # Both rails, zero, and the smallest step either side of it.
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (0x7FF0, 127.9375),
            (0x6400, 100.0),
            (0x3200, 50.0),
            (0x1900, 25.0),
            (0x0040, 0.25),
            (0x0000, 0.0),
            (0xFFC0, -0.25),
            (0xE700, -25.0),
            (0xC900, -55.0),
            (0x8000, -128.0),
        ],
    )
    def test_a_reading_converts(self, raw: int, expected: float) -> None:
        assert celsius(raw) == expected


@pytest.mark.parametrize("part", PARTS.values(), ids=PARTS.keys())
class TestRegisters:
    def test_registers_as_written_have_no_fault(self, part: Part) -> None:
        assert register_faults(part, written(part)) == []

    def test_a_flipped_limit_bit_is_named(self, part: Part) -> None:
        registers = written(part)
        registers[T_HIGH] ^= 0x0100
        assert register_faults(part, registers) == ["register 0x03 reads 0x5100, not 0x5000"]

    def test_a_flipped_configuration_bit_is_a_fault(self, part: Part) -> None:
        registers = written(part)
        registers[CONFIGURATION] ^= 0x01
        assert len(register_faults(part, registers)) == 1

    def test_the_mock_reads_back_what_setup_writes(self, part: Part) -> None:
        sensor = MockSensor()
        for pointer, value in written(part).items():
            sensor.write_register(pointer, value)
        assert register_faults(part, {pointer: sensor.read_register(pointer) for pointer in written(part)}) == []


@pytest.mark.parametrize(
    ("name", "unstored"),
    [("tmp100", 0x80), ("tmp112", 0x8000), ("tmp112", 0x0020)],
)
def test_the_one_shot_and_alert_bits_are_not_faults(name: str, unstored: int) -> None:
    part = PARTS[name]
    registers = written(part)
    registers[CONFIGURATION] ^= unstored
    assert register_faults(part, registers) == []
