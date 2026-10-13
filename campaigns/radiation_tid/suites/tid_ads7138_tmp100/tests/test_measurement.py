"""What the profile refuses, and how one sample judges both parts."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from suite.adc import AdcError, MockAdc
from suite.analyzer import MockAnalyzer
from suite.profile import TidAds7138Tmp100Profile
from suite.runner import (
    ADC_CONFIGURATION,
    INPUT_HIGH,
    INPUT_LOW,
    OUTPUT_HIGH,
    OUTPUT_LOW,
    PULSE,
    SENSOR_CONFIGURATION,
    TOGGLE,
    _check_adc,
    _check_sensor,
    sensor_register_faults,
)
from suite.sensor import CONFIGURATION, MockSensor, SensorError


class TestProfile:
    def test_the_two_parts_cannot_share_an_address(self) -> None:
        with pytest.raises(ValidationError, match="cannot share an address"):
            TidAds7138Tmp100Profile(ads7138_address=0x49, tmp100_address=0x49)

    def test_an_address_the_tmp100_cannot_be_strapped_to_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="TMP100 cannot be strapped to 0x50"):
            TidAds7138Tmp100Profile(tmp100_address=0x50)

    def test_temperature_bounds_out_of_order_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="min_c must be below max_c"):
            TidAds7138Tmp100Profile(min_c=40.0, max_c=10.0)


class TestSensorRegisters:
    def test_the_live_alert_bit_is_not_a_fault(self) -> None:
        registers = dict(SENSOR_CONFIGURATION)
        registers[CONFIGURATION] |= 0x80
        assert sensor_register_faults(registers) == []

    def test_a_configuration_that_changed_is_named(self) -> None:
        registers = dict(SENSOR_CONFIGURATION)
        registers[CONFIGURATION] = 0x0000
        assert sensor_register_faults(registers) == ["register 0x01 reads 0x0, not 0x60"]


def _no_ack(*args: object) -> int:
    """A part that has stopped answering."""
    raise AdcError("no ACK")


def _no_ack_sensor(*args: object) -> int:
    """A sensor that has stopped answering."""
    raise SensorError("no ACK")


def _board(adc: MockAdc, sensor: MockSensor) -> Any:
    """Both parts set up as `_setup` leaves them, behind a context the checks can read."""
    for register, value in ADC_CONFIGURATION.items():
        adc.write_register(register, value)
    for pointer, value in SENSOR_CONFIGURATION.items():
        sensor.write_register(pointer, value)
    analyzer = MockAnalyzer(adc, {TOGGLE: 2, OUTPUT_LOW: 3, OUTPUT_HIGH: 5, PULSE: 6})
    return SimpleNamespace(
        profile=TidAds7138Tmp100Profile(),
        extras={"adc": adc, "analyzer": analyzer, "sensor": sensor},
    )


class TestSample:
    def test_both_parts_on_a_healthy_board_pass(self) -> None:
        ctx = _board(MockAdc(high=INPUT_HIGH, low=INPUT_LOW, code=2296), MockSensor())
        for iteration in range(2):
            assert _check_adc(ctx, iteration, [])[0] == []
            assert _check_sensor(ctx, [])[0] == []

    def test_a_silent_adc_still_leaves_the_sensor_checked(self) -> None:
        ctx = _board(MockAdc(high=INPUT_HIGH, low=INPUT_LOW, code=2296), MockSensor())
        ctx.extras["adc"].write_register = _no_ack
        adc_faults, adc_metrics = _check_adc(ctx, 0, [])
        sensor_faults, sensor_metrics = _check_sensor(ctx, [])
        assert adc_faults == ["ADS7138: no ACK"]
        assert adc_metrics["answered"] == 0
        assert sensor_faults == []
        assert sensor_metrics["temperature_c"] == 25.0

    def test_a_silent_sensor_still_leaves_the_adc_checked(self) -> None:
        ctx = _board(MockAdc(high=INPUT_HIGH, low=INPUT_LOW, code=2296), MockSensor())
        ctx.extras["sensor"].read_register = _no_ack_sensor
        adc_faults, adc_metrics = _check_adc(ctx, 0, [])
        sensor_faults, _ = _check_sensor(ctx, [])
        assert adc_faults == []
        assert adc_metrics["ain0_v"] == pytest.approx(1.85, abs=0.001)
        assert sensor_faults == ["TMP100: no ACK"]
