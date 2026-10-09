"""What the profile refuses, how a reading converts, and what a register is judged against."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from suite.profile import TidTmp100Profile
from suite.runner import _REGISTERS, register_faults
from suite.sensor import CONFIGURATION, T_HIGH, MockTmp100, celsius


class TestProfile:
    def test_an_address_the_straps_cannot_select_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            TidTmp100Profile(address=0x10)

    def test_bounds_out_of_order_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="min_c must be below max_c"):
            TidTmp100Profile(min_c=40.0, max_c=10.0)


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


class TestRegisters:
    def test_registers_as_written_have_no_fault(self) -> None:
        assert register_faults(dict(_REGISTERS)) == []

    def test_the_one_shot_bit_is_not_a_fault(self) -> None:
        registers = dict(_REGISTERS)
        registers[CONFIGURATION] |= 0x80
        assert register_faults(registers) == []

    def test_a_flipped_limit_bit_is_named(self) -> None:
        registers = dict(_REGISTERS)
        registers[T_HIGH] ^= 0x0100
        assert register_faults(registers) == ["register 0x03 reads 0x5100, not 0x5000"]

    def test_the_mock_reads_back_what_setup_writes(self) -> None:
        sensor = MockTmp100()
        for pointer, value in _REGISTERS.items():
            sensor.write_register(pointer, value)
        assert register_faults({pointer: sensor.read_register(pointer) for pointer in _REGISTERS}) == []
