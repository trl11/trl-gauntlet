"""The drivers for real instruments, and the choice between them and the mocks.

Every test here runs against a stand-in for the device, so none of them needs
hardware attached.
"""

from __future__ import annotations

import ctypes
import itertools
import socket
import struct
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from gauntlet.capabilities import CapabilityRegistry, CommandRejected
from gauntlet.config import Settings
from gauntlet.instruments import cp2112_i2c as cp2112
from gauntlet.instruments import detect_instruments, is_simulated
from gauntlet.instruments.cp2112_i2c import Cp2112I2c
from gauntlet.instruments.di2008_daq import (
    Di2008Daq,
    Di2008Error,
    candidate_serials,
    decode_scans,
    mode_unit,
    open_usb,
    slist_word,
    strip_echo,
    value_from_code,
)
from gauntlet.instruments.fx2_logic import Fx2Logic
from gauntlet.instruments.hm310t_psu import (
    Hm310tPsu,
    ModbusError,
    modbus_crc,
    read_request,
    write_request,
)
from gauntlet.instruments.ni_daqmx import NiDaqmxDaq, NiDaqmxError


def _no_module(_: str) -> Any:
    """No NI-DAQmx device, whatever is plugged into the machine running this."""
    raise NiDaqmxError("NI-DAQmx lists no analog input device")


class _NiModule:
    """An NI-DAQmx module that answers, reduced to what the driver asks it."""

    def channels(self) -> tuple[str, ...]:
        return ("ai0",)

    def close(self) -> None:
        return None

    def identity(self) -> dict[str, str]:
        return {"chassis": "cDAQ1", "model": "NI 9238", "serial": "01AB23CD"}

    def name(self) -> str:
        return "cDAQ1Mod1"

    def rate_limits(self) -> tuple[float, float]:
        return (1613.0, 50000.0)

    def read(self, channels: tuple[tuple[str, float], ...], rate_hz: float, samples: int) -> list[list[float]]:
        return [[0.0] for _ in channels]

    def voltage_ranges(self) -> tuple[float, ...]:
        return (0.5,)


class _Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeSupply:
    """Enough of an HM310T to answer the frames the driver sends."""

    def __init__(self, **registers: int) -> None:
        self.registers = {0x0001: 0, 0x0010: 0, 0x0011: 0, 0x0030: 1200, 0x0031: 2000}
        self.registers.update({int(k, 0): v for k, v in registers.items()})
        self.closed = False
        self.requests: list[bytes] = []
        self._pending = b""

    def close(self) -> None:
        self.closed = True

    def read(self, size: int) -> bytes:
        taken, self._pending = self._pending[:size], self._pending[size:]
        return taken

    def reset_input_buffer(self) -> None:
        self._pending = b""

    def write(self, data: bytes) -> int:
        self.requests.append(data)
        self._pending += self._reply(data)
        return len(data)

    def _reply(self, request: bytes) -> bytes:
        slave, function = request[0], request[1]
        address = int.from_bytes(request[2:4], "big")
        if function == 0x03:
            count = int.from_bytes(request[4:6], "big")
            missing = [address + at for at in range(count) if address + at not in self.registers]
            if missing:
                # Function code with the high bit set, exception 2: bad address.
                body = bytes((slave, function | 0x80, 0x02))
                return body + modbus_crc(body)
            payload = b"".join((self.registers[address + at]).to_bytes(2, "big") for at in range(count))
            body = bytes((slave, function, 2 * count)) + payload
            return body + modbus_crc(body)
        self.registers[address] = int.from_bytes(request[4:6], "big")
        return request


class _FakeDaq:
    """Enough of a DI-2008 to answer info queries and stream scans."""

    # What a real DI-2008 answers, bar the clock, which follows the scan list.
    INFO: ClassVar[dict[str, str]] = {"0": "DATAQ", "1": "2008", "2": "76", "6": "6A046A27"}

    def __init__(
        self,
        clock: _Clock,
        codes: tuple[int, ...] = (),
        scans: int = 8,
        clock_hz: str | None = None,
        echoes_start: bool = True,
        stream: Iterator[tuple[int, ...]] | None = None,
        stream_every: int = 2,
    ) -> None:
        self.clock = clock
        self.closed = False
        self.commands: list[str] = []
        self._clock_hz = clock_hz
        self._codes = codes
        self._echoes_start = echoes_start
        self._pending = b""
        self._scanning = False
        self._scans = scans
        self._stream = stream
        self._reads = 0
        self._every = stream_every
        self._slots: dict[str, str] = {}

    def close(self) -> None:
        self.closed = True

    def leave_stray(self, data: bytes) -> None:
        """Put bytes in the endpoint, as an interrupted scan would."""
        self._pending += data

    def read(self, size: int, timeout_ms: int) -> bytes:
        # Every read costs time, so a capture loop bounded by the clock ends.
        self.clock.advance(0.05)
        self._reads += 1
        # A real unit times out between packets, which is what ends a drain.
        if self._scanning and self._stream is not None and self._reads % self._every == 1:
            codes = next(self._stream, None)
            if codes is not None:
                self._pending += struct.pack(f"<{len(codes)}h", *codes)
        elif self._scanning and self._scans:
            self._scans -= 1
            self._pending += struct.pack(f"<{len(self._codes)}h", *self._codes)
        taken, self._pending = self._pending[:size], self._pending[size:]
        return taken

    def serial_number(self) -> str:
        return "USB-SERIAL"

    def write(self, data: bytes) -> None:
        line = data.decode("ascii").strip("\r")
        self.commands.append(line)
        if line == "start":
            self._scanning = True
            if self._echoes_start:
                self._pending += b"start\r"
        elif line == "stop":
            self._scanning = False
            self._pending = b""
        elif line.startswith("slist "):
            _, slot, word = line.split()
            if word == "65535":
                self._slots = {key: value for key, value in self._slots.items() if int(key) < int(slot)}
            else:
                self._slots[slot] = word
        elif line.startswith("info "):
            number = line.split()[1]
            # A unit scanning one channel runs an order of magnitude faster.
            derived = "8000" if len(self._slots) == 1 else "800"
            answer = (
                (derived if self._clock_hz is None else self._clock_hz) if number == "9" else self.INFO.get(number, "")
            )
            self._pending += f"{line} {answer}\r".encode("ascii")


class _StubProvider:
    """A provider that reports itself as hardware and answers as told to."""

    name = "psu"

    def __init__(self, *, available: bool) -> None:
        self._available = available
        self.closed = False

    def available(self) -> bool:
        return self._available

    def close(self) -> None:
        self.closed = True

    def describe(self) -> dict[str, str]:
        return {"driver": "stub", "kind": "psu", "model": "stub-psu"}

    def instance_id(self) -> str:
        return "psu0"


def _psu(supply: _FakeSupply, clock: _Clock | None = None) -> Hm310tPsu:
    return Hm310tPsu("/dev/fake", clock=clock or _Clock(), open_port=lambda _: supply)


def _daq(transport: _FakeDaq, clock: _Clock) -> Di2008Daq:
    return Di2008Daq(clock=clock, open_transport=lambda _: transport)


def _readout_label(daq: Di2008Daq, key: str) -> str:
    """The label the provider declares for one readout, as the UI reads it."""
    return next(entry["label"] for entry in daq.readouts() if entry["key"] == key)


class TestModbusFraming:
    def test_crc_matches_the_published_check_value(self) -> None:
        # CRC-16/MODBUS over "123456789" is 0x4B37, appended low byte first.
        assert modbus_crc(b"123456789") == b"\x37\x4b"

    def test_crc_of_a_known_request(self) -> None:
        assert modbus_crc(bytes.fromhex("010300000001")) == b"\x84\x0a"

    def test_read_request_is_slave_function_address_count_crc(self) -> None:
        assert read_request(0x0030, 2).hex() == "010300300002c404"

    def test_write_request_echoes_address_and_value(self) -> None:
        assert write_request(0x0001, 1).hex() == "01060001000119ca"

    def test_a_corrupted_reply_is_refused(self) -> None:
        supply = _FakeSupply()
        psu = _psu(supply)
        assert psu.available()
        # Flip a bit in the reply so its CRC no longer covers it.
        original_write = supply.write

        def corrupt(data: bytes) -> int:
            written = original_write(data)
            supply._pending = bytes([supply._pending[0] ^ 0xFF]) + supply._pending[1:]
            return written

        supply.write = corrupt  # type: ignore[method-assign]
        assert psu.state()["voltage"] is None

    def test_an_exception_reply_raises(self) -> None:
        psu = _psu(_FakeSupply())
        assert psu.available()
        with pytest.raises(ModbusError):
            psu._transact(read_request(0x0099), 0x03)


class TestHm310tPsu:
    def test_reads_scale_into_volts_and_amps(self) -> None:
        supply = _FakeSupply(**{"0x0010": 1195, "0x0011": 1320, "0x0001": 1})
        state = _psu(supply).state()
        assert state["voltage"] == 11.95
        assert state["current"] == 1.32
        assert state["output_enabled"] is True
        assert state["voltage_setpoint"] == 12.0
        assert state["current_limit"] == 2.0

    def test_power_is_the_product_of_the_two_readings(self) -> None:
        supply = _FakeSupply(**{"0x0010": 1200, "0x0011": 2000})
        assert _psu(supply).state()["power"] == 24.0

    def test_state_is_all_none_while_nothing_answers(self) -> None:
        def refuse(_: str) -> Any:
            raise OSError("no such port")

        psu = Hm310tPsu("/dev/fake", clock=_Clock(), open_port=refuse)
        assert psu.available() is False
        assert set(psu.state().values()) == {None}
        assert "no such port" in psu.describe()["unavailable_reason"]

    def test_setting_a_voltage_writes_centivolts(self) -> None:
        supply = _FakeSupply()
        psu = _psu(supply)
        psu.command("set_voltage", {"voltage": 5.5})
        assert supply.registers[0x0030] == 550

    def test_setting_a_current_limit_writes_milliamps(self) -> None:
        supply = _FakeSupply()
        _psu(supply).command("set_current_limit", {"current": 1.25})
        assert supply.registers[0x0031] == 1250

    def test_enabling_the_output_writes_the_enable_register(self) -> None:
        supply = _FakeSupply()
        psu = _psu(supply)
        psu.command("set_output", {"enabled": True})
        assert supply.registers[0x0001] == 1
        psu.command("set_output", {"enabled": False})
        assert supply.registers[0x0001] == 0

    def test_an_out_of_range_voltage_is_refused(self) -> None:
        psu = _psu(_FakeSupply())
        with pytest.raises(CommandRejected):
            psu.command("set_voltage", {"voltage": 45.0})

    def test_an_unknown_command_is_refused(self) -> None:
        with pytest.raises(CommandRejected):
            _psu(_FakeSupply()).command("explode", {})

    def test_enabled_must_be_a_boolean(self) -> None:
        with pytest.raises(CommandRejected):
            _psu(_FakeSupply()).command("set_output", {"enabled": 1})

    def test_a_supply_that_stops_answering_is_reprobed_on_an_interval(self) -> None:
        clock = _Clock()
        supply = _FakeSupply()
        psu = _psu(supply, clock)
        assert psu.available()
        psu.close()
        # Within the interval the cached answer stands and the port stays shut.
        assert psu.available() is False
        clock.advance(3.0)
        assert psu.available() is True

    def test_readouts_describe_one_channel(self) -> None:
        keys = [entry["key"] for entry in _psu(_FakeSupply()).readouts()]
        assert keys == ["voltage", "current", "power", "voltage_setpoint", "current_limit", "output_enabled"]

    def test_the_output_command_is_the_dangerous_one(self) -> None:
        psu = _psu(_FakeSupply())
        assert psu.primary_command() == "set_output"
        output = next(c for c in psu.commands() if c["name"] == "set_output")
        assert output["danger"] is True

    def test_closing_leaves_the_output_alone(self) -> None:
        supply = _FakeSupply(**{"0x0001": 1})
        psu = _psu(supply)
        assert psu.available()
        psu.close()
        assert supply.closed is True
        assert supply.registers[0x0001] == 1


class TestDi2008Protocol:
    def test_slist_word_packs_the_mode_above_the_channel(self) -> None:
        assert slist_word(0, "10v") == 0x0800
        assert slist_word(7, "10v") == 0x0807
        assert slist_word(3, "tc_k") == 0x0303
        assert slist_word(2, "25mv") == 0x1002

    def test_an_unknown_mode_is_refused(self) -> None:
        with pytest.raises(ValueError, match="unknown mode"):
            slist_word(0, "9000v")

    def test_a_voltage_code_scales_by_its_full_scale(self) -> None:
        assert value_from_code(32768, "10v") == 10.0
        assert value_from_code(-32768, "10v") == -10.0
        assert value_from_code(16384, "5v") == 2.5
        assert value_from_code(0, "1v") == 0.0

    def test_a_thermocouple_code_is_tenths_of_a_degree_in_32_steps(self) -> None:
        assert value_from_code(32 * 250, "tc_k") == 25.0
        assert value_from_code(0, "tc_j") == 0.0
        assert value_from_code(-32 * 100, "tc_t") == -10.0

    def test_units_follow_the_mode(self) -> None:
        assert mode_unit("10v") == "V"
        assert mode_unit("tc_k") == "C"

    def test_decode_splits_the_stream_into_scans(self) -> None:
        payload = struct.pack("<6h", 1, 2, 3, 4, 5, 6)
        assert decode_scans(payload, 3) == [(1, 2, 3), (4, 5, 6)]

    def test_a_partial_trailing_scan_is_dropped(self) -> None:
        payload = struct.pack("<5h", 1, 2, 3, 4, 5)
        assert decode_scans(payload, 3) == [(1, 2, 3)]

    def test_too_little_for_one_scan_decodes_to_nothing(self) -> None:
        assert decode_scans(struct.pack("<2h", 1, 2), 3) == []

    def test_the_start_echo_is_stripped(self) -> None:
        assert strip_echo(b"start\r\x01\x02") == b"\x01\x02"

    def test_a_capture_with_no_echo_is_left_alone(self) -> None:
        payload = bytes(40) + b"\r"
        assert strip_echo(payload) == payload

    def test_a_sample_carrying_a_carriage_return_survives(self) -> None:
        # 0x0D is the low byte of code 13, which is 4 mV on the widest range.
        # Cutting there would shift every channel onto its neighbour.
        payload = struct.pack("<3h", 13, 3341, 2)
        assert strip_echo(payload) == payload


class TestDi2008Daq:
    def test_connecting_configures_the_scan_list_and_rate(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock)
        daq = _daq(transport, clock)
        assert daq.available() is True
        assert "slist 0 2048" in transport.commands
        assert "slist 7 2055" in transport.commands
        assert "srate 4" in transport.commands
        assert "dec 1" in transport.commands
        # Without this the device holds samples back until a packet is full.
        assert "ps 0" in transport.commands

    def test_packet_size_is_set_after_the_rate_so_a_slow_scan_is_not_held_back(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock)
        _daq(transport, clock).available()
        # srate puts the packet size back, and at a slow rate a full packet
        # is minutes of samples.
        assert transport.commands.index("ps 0") > max(
            at for at, line in enumerate(transport.commands) if line.startswith("srate ")
        )

    def test_sampling_decodes_every_channel(self) -> None:
        clock = _Clock()
        codes = tuple(range(0, 8 * 4096, 4096))
        daq = _daq(_FakeDaq(clock, codes), clock)
        channels = daq.command("sample", {})["channels"]
        assert channels["1"] == 0.0
        assert channels["2"] == value_from_code(4096, "10v")
        assert len(channels) == 8

    def test_state_reports_the_mode_and_unit_of_each_channel(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock, tuple(range(8))), clock)
        channel = daq.state()["channels"]["1"]
        assert channel["mode"] == "10v"
        assert channel["unit"] == "V"

    def test_setting_a_mode_reloads_the_scan_list(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, tuple(range(8)))
        daq = _daq(transport, clock)
        daq.command("configure", {"rows": {"3": {"mode": "tc_k"}}})
        assert f"slist 2 {slist_word(2, 'tc_k')}" in transport.commands
        assert daq.state()["channels"]["3"]["unit"] == "C"

    def test_a_thermocouple_channel_reads_in_celsius(self) -> None:
        clock = _Clock()
        codes = tuple(32 * 250 for _ in range(8))
        daq = _daq(_FakeDaq(clock, codes), clock)
        daq.command("configure", {"rows": {"1": {"mode": "tc_k"}}})
        assert daq.command("sample", {})["channels"]["1"] == 25.0

    def test_every_channel_is_settled_in_one_pass(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, tuple(range(8)))
        daq = _daq(transport, clock)
        assert daq.available()
        before = transport.commands.count("ps 0")
        result = daq.command(
            "configure",
            {"rows": {str(n): {"label": f"Rail {n}", "mode": "5v"} for n in range(1, 9)}},
        )
        assert [entry["mode"] for entry in result["channels"].values()] == ["5v"] * 8
        assert [entry["label"] for entry in result["channels"].values()] == [f"Rail {n}" for n in range(1, 9)]
        # Eight channels, one scan list reload, not eight.
        assert transport.commands.count("ps 0") == before + 1

    def test_a_channel_no_row_names_is_left_alone(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock, tuple(range(8))), clock)
        daq.command("configure", {"rows": {"1": {"label": "Rail 5V", "mode": "1v"}}})
        daq.command("configure", {"rows": {"2": {"mode": "tc_k"}}})
        assert daq.state()["channels"]["1"]["label"] == "Rail 5V"
        assert daq.state()["channels"]["1"]["mode"] == "1v"

    def test_a_row_may_carry_either_setting_on_its_own(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock, tuple(range(8))), clock)
        daq.command("configure", {"rows": {"1": {"mode": "tc_k"}}})
        daq.command("configure", {"rows": {"1": {"label": "Ambient"}}})
        channel = daq.state()["channels"]["1"]
        assert channel == {"enabled": True, "label": "Ambient", "mode": "tc_k", "unit": "C", "value": channel["value"]}

    def test_a_channel_reads_as_its_number_until_it_is_named(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock, tuple(range(8))), clock)
        assert daq.state()["channels"]["3"]["label"] == "CH 3"
        assert _readout_label(daq, "channels.3.value") == "CH 3"

    def test_naming_a_channel_renames_its_reading_everywhere(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock, tuple(range(8))), clock)
        daq.command("configure", {"rows": {"3": {"label": "Rail 3V3"}}})
        assert daq.state()["channels"]["3"]["label"] == "Rail 3V3"
        # The panel, the dashboard tile and the chart legend all read this one.
        assert _readout_label(daq, "channels.3.value") == "Rail 3V3"

    def test_naming_a_channel_reconfigures_nothing(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, tuple(range(8)))
        daq = _daq(transport, clock)
        assert daq.available()
        before = list(transport.commands)
        daq.command("configure", {"rows": {"1": {"label": "Rail 5V"}}})
        # A label is what a reading is called, not how it is taken.
        assert transport.commands == before

    def test_an_empty_label_puts_a_channel_back_to_its_number(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock, tuple(range(8))), clock)
        daq.command("configure", {"rows": {"2": {"label": "Shunt"}}})
        daq.command("configure", {"rows": {"2": {"label": "  "}}})
        assert daq.state()["channels"]["2"]["label"] == "CH 2"
        assert _readout_label(daq, "channels.2.value") == "CH 2"

    def test_a_label_is_trimmed_to_one_line_of_ordinary_spacing(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock, tuple(range(8))), clock)
        result = daq.command("configure", {"rows": {"1": {"label": "  Rail\n\t3V3  "}}})
        assert result["channels"]["1"]["label"] == "Rail 3V3"

    def test_a_label_longer_than_a_panel_holds_is_cut(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock, tuple(range(8))), clock)
        result = daq.command("configure", {"rows": {"1": {"label": "x" * 80}}})
        assert len(result["channels"]["1"]["label"]) == 32

    def test_labelling_an_unknown_channel_is_refused(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock), clock)
        with pytest.raises(CommandRejected):
            daq.command("configure", {"rows": {"99": {"label": "Rail 3V3"}}})

    def test_a_row_that_is_not_an_object_is_refused(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock), clock)
        with pytest.raises(CommandRejected, match="settings for channel '1' must be an object"):
            daq.command("configure", {"rows": {"1": "tc_k"}})

    def test_the_label_column_takes_free_text_rather_than_a_choice(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock), clock)
        command = next(entry for entry in daq.commands() if entry["name"] == "configure")
        label = next(field for field in command["fields"] if field["name"] == "label")
        assert label["type"] == "string"
        # No choices is what makes the operator UI draw a text box.
        assert label["choices"] == []

    def test_a_row_carries_what_its_channel_is_set_to_now(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock, tuple(range(8))), clock)
        daq.command("configure", {"rows": {"2": {"label": "Shunt", "mode": "tc_j"}}})
        command = next(entry for entry in daq.commands() if entry["name"] == "configure")
        row = next(entry for entry in command["rows"] if entry["key"] == "2")
        # The row is what the operator's controls start at, so an edit to one
        # channel does not blank out what the others are set to.
        assert row == {"key": "2", "label": "CH 2", "values": {"enabled": True, "label": "Shunt", "mode": "tc_j"}}

    def test_a_row_offers_an_empty_label_rather_than_the_channel_number(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock), clock)
        command = next(entry for entry in daq.commands() if entry["name"] == "configure")
        row = next(entry for entry in command["rows"] if entry["key"] == "1")
        # Offering "CH 1" would have the operator apply it back as a real label.
        assert row["values"]["label"] == ""

    def test_a_bad_row_leaves_every_channel_as_it_was(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock, tuple(range(8))), clock)
        with pytest.raises(CommandRejected):
            daq.command(
                "configure",
                {"rows": {"1": {"label": "Rail 5V"}, "2": {"mode": "9000v"}}},
            )
        # Checked before applied, so nothing is left half configured.
        assert daq.state()["channels"]["1"]["label"] == "CH 1"

    def test_configuring_nothing_is_refused(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock), clock)
        with pytest.raises(CommandRejected):
            daq.command("configure", {"rows": {}})

    def test_an_unknown_mode_is_refused(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock), clock)
        with pytest.raises(CommandRejected):
            daq.command("configure", {"rows": {"1": {"mode": "9000v"}}})

    def test_an_unknown_channel_is_refused(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock), clock)
        with pytest.raises(CommandRejected):
            daq.command("configure", {"rows": {"99": {"mode": "10v"}}})

    def test_state_is_all_none_while_nothing_answers(self) -> None:
        def refuse(_: str) -> Any:
            raise OSError("no DI-2008")

        daq = Di2008Daq(clock=_Clock(), open_transport=refuse)
        assert daq.available() is False
        values = [c["value"] for c in daq.state()["channels"].values()]
        assert values == [None] * 8
        assert "no DI-2008" in daq.describe()["unavailable_reason"]

    def test_the_scan_rate_divides_the_clock_the_device_reports(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, clock_hz="800")
        daq = Di2008Daq(clock=clock, dec=10, srate=4, open_transport=lambda _: transport)
        assert daq.available()
        # The clock for a list of more than one channel is 800, not the 8000
        # the base clock alone would suggest: 800 / (4 * 10) across eight.
        assert daq.scan_rate_hz() == 2.5

    def test_the_clock_is_read_back_after_the_scan_list_is_loaded(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, clock_hz="8000")
        daq = Di2008Daq(clock=clock, dec=1, srate=4, open_transport=lambda _: transport)
        assert daq.available()
        assert daq.scan_rate_hz() == 250.0
        assert transport.commands.index("info 9") > transport.commands.index("slist 7 2055")

    def test_a_device_that_will_not_name_a_clock_keeps_the_base_one(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, clock_hz="")
        daq = Di2008Daq(clock=clock, dec=10, srate=4, open_transport=lambda _: transport)
        assert daq.available()
        assert daq.scan_rate_hz() == 25.0

    def test_a_stray_sample_is_drained_before_a_scan(self) -> None:
        clock = _Clock()
        codes = tuple(range(0, 8 * 4096, 4096))
        transport = _FakeDaq(clock, codes, echoes_start=False)
        daq = _daq(transport, clock)
        assert daq.available()
        # Left in the endpoint this would be read as channel one and rotate
        # every reading onto the channel beside it.
        transport.leave_stray(struct.pack("<h", 999))
        channels = daq.command("sample", {})["channels"]
        assert channels["1"] == 0.0
        assert channels["2"] == value_from_code(4096, "10v")

    def test_identity_comes_from_the_device(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock), clock)
        assert daq.available()
        assert daq.describe()["serial"] == "6A046A27"
        assert daq.describe()["firmware"] == "76"
        assert "0683:2008" in daq.connection()

    def test_closing_stops_the_scan_and_releases_the_device(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock)
        daq = _daq(transport, clock)
        assert daq.available()
        daq.close()
        assert transport.closed is True
        assert transport.commands[-1] == "stop"


class _FakeI2cBus:
    """Stands in for the ``I2C_RDWR`` ioctl, answering a read from ``values``."""

    def __init__(self, values: dict[int, bytes] | None = None) -> None:
        self.values = values or {}
        self.calls: list[list[tuple[int, int, bytes]]] = []
        self.fails = False
        # Addresses that NAK a probe, for a scan told apart from a plain read.
        self.absent: set[int] = set()

    def ioctl(self, fd: int, request: int, argp: cp2112._I2cRdwrData) -> int:
        assert request == cp2112._I2C_RDWR
        if self.fails:
            raise OSError("no device answered")
        if argp.nmsgs == 1 and argp.msgs[0].addr in self.absent:
            raise OSError("no device answered")
        data = argp
        messages = []
        for i in range(data.nmsgs):
            msg = data.msgs[i]
            buf = ctypes.cast(msg.buf, ctypes.POINTER(ctypes.c_uint8 * msg.len)).contents
            if msg.flags & cp2112._I2C_M_RD:
                reply = self.values.get(msg.addr, b"")
                for j in range(msg.len):
                    buf[j] = reply[j] if j < len(reply) else 0
            messages.append((msg.addr, msg.flags, bytes(buf)))
        self.calls.append(messages)
        return 0


def _bridge(monkeypatch: Any, bus: _FakeI2cBus) -> Cp2112I2c:
    """A ``Cp2112I2c`` whose node is there, opens, and whose ioctls hit ``bus``."""
    monkeypatch.setattr(cp2112.os, "access", lambda node, mode: True)
    monkeypatch.setattr(cp2112.os, "open", lambda node, flags: 7)
    monkeypatch.setattr(cp2112.os, "close", lambda fd: None)
    monkeypatch.setattr(cp2112.fcntl, "ioctl", bus.ioctl)
    return Cp2112I2c("/dev/i2c-9")


class TestCp2112I2c:
    def test_read_returns_the_bytes_the_device_answered(self, monkeypatch: Any) -> None:
        bridge = _bridge(monkeypatch, _FakeI2cBus({0x48: bytes([0x09, 0x60])}))
        result = bridge.command("read", {"address": 0x48, "length": 2})
        assert result["data_hex"] == "09 60"
        assert result["direction"] == "read"
        assert result["length"] == 2

    def test_write_sends_the_given_bytes_and_no_read(self, monkeypatch: Any) -> None:
        bus = _FakeI2cBus()
        bridge = _bridge(monkeypatch, bus)
        bridge.command("write", {"address": 0x48, "data": "01 02"})
        assert bus.calls[-1] == [(0x48, 0, b"\x01\x02")]

    def test_write_read_is_one_transaction_with_no_stop_between(self, monkeypatch: Any) -> None:
        bus = _FakeI2cBus({0x48: b"\x00\x64"})
        bridge = _bridge(monkeypatch, bus)
        result = bridge.command("write_read", {"address": 0x48, "data": "00", "read_length": 2})
        assert len(bus.calls[-1]) == 2
        assert bus.calls[-1][0] == (0x48, 0, b"\x00")
        assert bus.calls[-1][1][0] == 0x48
        assert bus.calls[-1][1][1] & cp2112._I2C_M_RD
        assert result["data_hex"] == "00 64"

    def test_write_read_s_own_length_is_independent_of_what_data_wrote(self, monkeypatch: Any) -> None:
        # The common register-read shape: one byte names the register, eight
        # come back, and the two counts have nothing to do with each other.
        bus = _FakeI2cBus({0x18: bytes(range(8))})
        bridge = _bridge(monkeypatch, bus)
        result = bridge.command("write_read", {"address": 0x18, "data": "00", "read_length": 8})
        assert bus.calls[-1][0] == (0x18, 0, b"\x00")
        assert len(bus.calls[-1][1][2]) == 8
        assert result["length"] == 8

    def test_state_reflects_the_last_transaction(self, monkeypatch: Any) -> None:
        bridge = _bridge(monkeypatch, _FakeI2cBus({0x48: b"\xab"}))
        state = bridge.state()
        assert state["known_addresses"] == []
        assert {value for key, value in state.items() if key != "known_addresses"} == {None}
        bridge.command("read", {"address": 0x48, "length": 1})
        assert bridge.state()["data_hex"] == "ab"

    def test_an_out_of_range_address_is_refused(self, monkeypatch: Any) -> None:
        bridge = _bridge(monkeypatch, _FakeI2cBus())
        with pytest.raises(CommandRejected):
            bridge.command("read", {"address": 0x80, "length": 1})

    def test_bad_hex_is_refused(self, monkeypatch: Any) -> None:
        bridge = _bridge(monkeypatch, _FakeI2cBus())
        with pytest.raises(CommandRejected):
            bridge.command("write", {"address": 0x48, "data": "zz"})

    def test_an_unknown_command_is_refused(self, monkeypatch: Any) -> None:
        bridge = _bridge(monkeypatch, _FakeI2cBus())
        with pytest.raises(CommandRejected):
            bridge.command("explode", {"address": 0x48})

    def test_a_transfer_failure_is_reported_and_closes_the_node(self, monkeypatch: Any) -> None:
        bus = _FakeI2cBus()
        bus.fails = True
        bridge = _bridge(monkeypatch, bus)
        with pytest.raises(CommandRejected):
            bridge.command("read", {"address": 0x48, "length": 1})
        assert bridge._fd is None
        # A device that does not answer says nothing about the bridge.
        assert bridge.available() is True

    def test_unavailable_when_the_node_cannot_be_opened(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(cp2112.os, "access", lambda node, mode: False)
        bridge = Cp2112I2c("/dev/i2c-9")
        assert bridge.available() is False
        assert "/dev/i2c-9" in bridge.describe()["unavailable_reason"]

    def test_the_node_is_held_only_for_the_length_of_a_command(self, monkeypatch: Any) -> None:
        # hid-cp2112 cannot remove an unplugged bridge while its adapter is
        # open, and USB hotplug on the host stalls until it can.
        opened: list[str] = []
        closed: list[int] = []
        bridge = _bridge(monkeypatch, _FakeI2cBus({0x48: b"\xab"}))
        monkeypatch.setattr(cp2112.os, "open", lambda node, flags: opened.append(node) or 7)
        monkeypatch.setattr(cp2112.os, "close", closed.append)
        assert bridge.available()
        bridge.state()
        assert opened == []
        bridge.command("read", {"address": 0x48, "length": 1})
        bridge.command("scan", {})
        assert opened == ["/dev/i2c-9", "/dev/i2c-9"]
        assert closed == [7, 7]
        assert bridge._fd is None

    def test_connection_names_the_node(self, monkeypatch: Any) -> None:
        bridge = _bridge(monkeypatch, _FakeI2cBus())
        assert bridge.connection() == "/dev/i2c-9 (I2C/SMBus)"

    def test_read_is_the_primary_command(self, monkeypatch: Any) -> None:
        assert _bridge(monkeypatch, _FakeI2cBus()).primary_command() == "read"

    def test_scan_finds_only_the_addresses_that_answer(self, monkeypatch: Any) -> None:
        bus = _FakeI2cBus()
        bus.absent = set(range(0x08, 0x78)) - {0x20, 0x50}
        bridge = _bridge(monkeypatch, bus)
        result = bridge.command("scan", {})
        assert result["known_addresses"] == [0x20, 0x50]

    def test_a_scan_is_remembered_in_state_until_the_next_one(self, monkeypatch: Any) -> None:
        bus = _FakeI2cBus()
        bus.absent = set(range(0x08, 0x78)) - {0x48}
        bridge = _bridge(monkeypatch, bus)
        assert bridge.state()["known_addresses"] == []
        bridge.command("scan", {})
        assert bridge.state()["known_addresses"] == [0x48]
        # A transaction elsewhere does not clear what the last scan found.
        bridge.command("read", {"address": 0x48, "length": 1})
        assert bridge.state()["known_addresses"] == [0x48]

    def test_the_address_field_offers_what_the_last_scan_found(self, monkeypatch: Any) -> None:
        fields = {f["name"]: f for f in _bridge(monkeypatch, _FakeI2cBus()).commands()[2]["fields"]}
        assert fields["address"]["choices_from"] == "known_addresses"
        assert fields["address"]["dial"] is False

    def test_write_read_s_length_is_its_own_field_not_read_s(self, monkeypatch: Any) -> None:
        # A bare read's "length" and a write-then-read's "read_length" are
        # named apart so the group shows both, typed independently, rather
        # than merging into one shared control neither use fits.
        fields = {f["name"]: f for f in _bridge(monkeypatch, _FakeI2cBus()).commands()[2]["fields"]}
        assert "read_length" in fields
        assert "length" not in fields

    def test_scan_does_not_disconnect_on_addresses_that_nak(self, monkeypatch: Any) -> None:
        bus = _FakeI2cBus()
        bus.absent = set(range(0x08, 0x78))
        bridge = _bridge(monkeypatch, bus)
        bridge.command("scan", {})
        assert bridge.available() is True

    def test_a_probe_reads_a_byte_rather_than_a_zero_length_read(self, monkeypatch: Any) -> None:
        # A real CP2112 rejects a zero-length read with EINVAL for every
        # address, present or not, so a scan built on one would find nothing
        # ever. The probe has to clock out at least one byte to get a real
        # ACK/NAK out of the adapter.
        bus = _FakeI2cBus()
        bus.absent = set(range(0x08, 0x78)) - {0x20}
        bridge = _bridge(monkeypatch, bus)
        bridge.command("scan", {})
        probe_calls = [call for call in bus.calls if len(call) == 1 and call[0][0] == 0x20]
        assert probe_calls
        assert probe_calls[-1][0][2] == b"\x00"  # one byte read, not zero

    def test_a_write_of_no_bytes_is_an_empty_message(self, monkeypatch: Any) -> None:
        bus = _FakeI2cBus()
        result = _bridge(monkeypatch, bus).command("write", {"address": 0x48, "data": " "})
        assert bus.calls[-1] == [(0x48, 0, b"")]
        assert result["length"] == 0

    def test_a_command_on_a_bridge_that_will_not_open_is_refused(self, monkeypatch: Any) -> None:
        def refuse(node: str, flags: int) -> int:
            raise OSError("no such device")

        monkeypatch.setattr(cp2112.os, "open", refuse)
        bridge = Cp2112I2c("/dev/i2c-9")
        with pytest.raises(CommandRejected, match="i2c is unavailable: cannot open /dev/i2c-9"):
            bridge.command("read", {"address": 0x48, "length": 1})

    def test_write_runs_a_command_and_returns_the_new_state(self, monkeypatch: Any) -> None:
        bridge = _bridge(monkeypatch, _FakeI2cBus())
        state = bridge.write({"command": "write", "args": {"address": 0x20, "data": "0a0b"}})
        assert state == {
            "address": 0x20,
            "data_hex": "0a 0b",
            "direction": "write",
            "length": 2,
            "known_addresses": [],
        }
        assert bridge.read() == state

    def test_it_names_itself_and_what_it_reads_out(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(cp2112.os, "open", lambda node, flags: 7)
        bridge = Cp2112I2c("/dev/i2c-9", instance="i2c-CP-1")
        assert bridge.instance_id() == "i2c-CP-1"
        assert [entry["key"] for entry in bridge.readouts()] == ["address", "direction", "data_hex", "length"]

    def test_a_close_that_fails_still_lets_go_of_the_node(self, monkeypatch: Any) -> None:
        bridge = _bridge(monkeypatch, _FakeI2cBus())

        def refuse(fd: int) -> None:
            raise OSError("bad file descriptor")

        monkeypatch.setattr(cp2112.os, "close", refuse)
        bridge.command("write", {"address": 0x48, "data": "00"})
        assert bridge._fd is None

    def test_without_a_node_open_nothing_is_probed_or_transferred(self, monkeypatch: Any) -> None:
        bus = _FakeI2cBus()
        bridge = _bridge(monkeypatch, bus)
        assert bridge._probe(0x48) is False
        with pytest.raises(CommandRejected, match="not connected"):
            bridge._transfer([(0x48, 0, b"\x00")])
        assert bus.calls == []


class TestCandidateAdapters:
    """Finding the CP2112 among the I2C adapters sysfs lists."""

    def _sysfs(self, monkeypatch: Any, tmp_path: Any) -> Any:
        """Point the lookups at a sysfs tree under ``tmp_path``, returned."""
        root = tmp_path / "sys"
        real_glob = cp2112.glob.glob

        def moved(path: str) -> str:
            return str(root) + path.removeprefix("/sys")

        monkeypatch.setattr(cp2112.glob, "glob", lambda pattern: real_glob(moved(pattern)))
        monkeypatch.setattr(cp2112, "Path", lambda path: Path(moved(path) if path.startswith("/sys/") else path))
        return root

    def _adapter(self, root: Any, adapter: str, name: str) -> None:
        folder = root / "class" / "i2c-dev" / adapter
        folder.mkdir(parents=True)
        (folder / "name").write_text(name + "\n")

    def _hidraw(self, root: Any, hidraw: str, uevent: str) -> None:
        folder = root / "class" / "hidraw" / hidraw / "device"
        folder.mkdir(parents=True)
        (folder / "uevent").write_text(uevent)

    def test_only_a_cp2112_adapter_is_a_candidate_and_carries_its_serial(self, monkeypatch: Any, tmp_path: Any) -> None:
        root = self._sysfs(monkeypatch, tmp_path)
        self._adapter(root, "i2c-0", "Synopsys DesignWare I2C adapter")
        self._adapter(root, "i2c-18", "CP2112 SMBus Bridge on hidraw3")
        self._hidraw(root, "hidraw3", "HID_ID=0003:000010C4:0000EA90\nHID_UNIQ=00A1B2C3\n")
        assert cp2112.candidate_adapters() == [("/dev/i2c-18", "00A1B2C3")]

    def test_an_adapter_whose_name_cannot_be_read_is_passed_over(self, monkeypatch: Any, tmp_path: Any) -> None:
        root = self._sysfs(monkeypatch, tmp_path)
        (root / "class" / "i2c-dev" / "i2c-5" / "name").mkdir(parents=True)
        assert cp2112.candidate_adapters() == []

    def test_a_bridge_without_a_serial_is_still_a_candidate(self, monkeypatch: Any, tmp_path: Any) -> None:
        root = self._sysfs(monkeypatch, tmp_path)
        self._adapter(root, "i2c-18", "CP2112 SMBus Bridge on hidraw3")
        self._hidraw(root, "hidraw3", "HID_ID=0003:000010C4:0000EA90\n")
        self._adapter(root, "i2c-19", "CP2112 SMBus Bridge on hidraw4")
        assert cp2112.candidate_adapters() == [("/dev/i2c-18", ""), ("/dev/i2c-19", "")]


class TestDetection:
    def _settings(self, tmp_path: Any, **overrides: Any) -> Settings:
        """Settings that look for nothing, bar what a test asks for."""
        return Settings(
            data_dir=tmp_path / "data",
            **{
                "camera_device": "",
                "daq_serial": "",
                "i2c_serial": "",
                "logic_serial": "",
                "psu_port": "",
                **overrides,
            },
        )

    def test_nothing_answering_registers_nothing(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path))
        assert registry.instance_keys() == []

    def test_a_simulation_is_registered_only_when_it_is_named(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, simulated_instruments=["psu"]))
        assert registry.instance_keys() == ["psu"]
        assert is_simulated(registry.provider("psu")) is True

    def test_the_chamber_exists_only_while_it_is_simulated(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, simulated_instruments=["chamber"]))
        assert registry.instance_keys() == ["chamber"]
        # There is no driver for a real chamber, so dropping the simulation
        # leaves nothing behind.
        detect_instruments(registry, self._settings(tmp_path))
        assert registry.instance_keys() == []

    def test_a_role_per_device_registers_one_instrument_each(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        settings = self._settings(
            tmp_path,
            psu_port={"left": "/dev/does-not-exist-a", "right": "/dev/does-not-exist-b"},
        )
        detect_instruments(registry, settings)
        assert registry.instance_keys() == ["psu.left", "psu.right"]

    def test_dropping_one_role_leaves_the_other_connected(self, tmp_path: Any) -> None:
        """Each role settles on its own, so losing one does not restart the other."""
        registry = CapabilityRegistry()
        both = self._settings(tmp_path, i2c_serial={"dut": "A", "ref": "B"}, simulated_instruments=["i2c"])
        detect_instruments(registry, both)
        kept = registry.provider("i2c.dut")
        detect_instruments(
            registry,
            self._settings(tmp_path, i2c_serial={"dut": "A"}, simulated_instruments=["i2c"]),
        )
        assert registry.instance_keys() == ["i2c.dut"]
        assert registry.provider("i2c.dut") is kept

    def test_dropping_the_roles_returns_to_one_instrument(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(
            registry,
            self._settings(tmp_path, psu_port={"left": "/dev/does-not-exist-a", "right": "/dev/does-not-exist-b"}),
        )
        detect_instruments(registry, self._settings(tmp_path, psu_port="/dev/does-not-exist-a"))
        assert registry.instance_keys() == ["psu"]

    def test_a_simulation_is_simulated_once_per_role(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(
            registry,
            self._settings(tmp_path, i2c_serial={"dut": "A", "ref": "B"}, simulated_instruments=["i2c"]),
        )
        assert registry.instance_keys() == ["i2c.dut", "i2c.ref"]
        # Each carries its own instance id, so the panel and the run manifest
        # name one bridge rather than the same one twice.
        assert registry.provider("i2c.dut").instance_id() != registry.provider("i2c.ref").instance_id()

    def test_two_units_on_auto_register_as_daq_0_and_daq_1(self, tmp_path: Any, monkeypatch: Any) -> None:
        monkeypatch.setattr("gauntlet.instruments.detect.candidate_serials", lambda: ["AAA", "BBB"])
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, daq_serial="auto"))
        assert registry.instance_keys() == ["daq.0", "daq.1"]
        assert [registry.provider(key).instance_id() for key in ("daq.0", "daq.1")] == ["daq0", "daq1"]
        assert [registry.provider(key).describe()["driver"] for key in ("daq.0", "daq.1")] == ["di2008"] * 2

    def test_each_unit_is_bound_to_its_own_serial(self, tmp_path: Any, monkeypatch: Any) -> None:
        monkeypatch.setattr("gauntlet.instruments.detect.candidate_serials", lambda: ["AAA", "BBB"])
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, daq_serial="auto"))
        assert registry.provider("daq.0")._serial_filter == "AAA"
        assert registry.provider("daq.1")._serial_filter == "BBB"

    def test_a_second_unit_arriving_replaces_the_bare_name(self, tmp_path: Any, monkeypatch: Any) -> None:
        serials = ["AAA"]
        monkeypatch.setattr("gauntlet.instruments.detect.candidate_serials", lambda: serials)
        monkeypatch.setattr(Di2008Daq, "available", lambda self: True)
        registry = CapabilityRegistry()
        settings = self._settings(tmp_path, daq_serial="auto")
        detect_instruments(registry, settings)
        assert registry.instance_keys() == ["daq"]
        serials.append("BBB")
        detect_instruments(registry, settings)
        assert registry.instance_keys() == ["daq.0", "daq.1"]

    def test_a_named_role_gets_an_instance_id_of_its_own(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, daq_serial={"dut": "AAA", "ref": "BBB"}))
        assert registry.provider("daq.dut").instance_id() == "daq-dut"
        assert registry.provider("daq.ref").instance_id() == "daq-ref"

    def test_the_bench_default_reaches_the_registry(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(
            registry,
            self._settings(
                tmp_path,
                i2c_serial={"dut": "A", "ref": "B"},
                default_instruments={"i2c": "ref"},
                simulated_instruments=["i2c"],
            ),
        )
        assert registry.resolve("i2c") == ["i2c.ref"]

    def test_a_named_port_registers_the_driver_even_when_it_is_silent(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, psu_port="/dev/does-not-exist"))
        psu = registry.provider("psu")
        assert is_simulated(psu) is False
        assert psu.available() is False
        assert "/dev/does-not-exist" in psu.describe()["unavailable_reason"]

    def test_a_named_analyzer_registers_even_before_it_is_on_the_bus(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, logic_serial="NOTHERE"))
        analyzer = registry.provider("logic")
        assert is_simulated(analyzer) is False
        assert analyzer.available() is False

    def test_a_working_device_is_never_rebuilt(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        live = _StubProvider(available=True)
        registry.register(live)
        detect_instruments(registry, self._settings(tmp_path))
        assert registry.provider("psu") is live
        assert live.closed is False

    def test_a_device_that_stopped_answering_is_dropped(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        dead = _StubProvider(available=False)
        registry.register(dead)
        detect_instruments(registry, self._settings(tmp_path))
        assert registry.provider("psu") is None
        assert dead.closed is True

    def test_a_simulation_does_not_displace_a_working_device(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        live = _StubProvider(available=True)
        registry.register(live)
        detect_instruments(registry, self._settings(tmp_path, simulated_instruments=["psu"]))
        assert registry.provider("psu") is live

    def test_a_scan_does_not_restart_a_simulation(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        settings = self._settings(tmp_path, simulated_instruments=["psu"])
        detect_instruments(registry, settings)
        before = registry.provider("psu")
        detect_instruments(registry, settings)
        assert registry.provider("psu") is before


class _FakeEndpoint:
    """One bulk endpoint, recording what the driver put on it."""

    def __init__(self, direction: int, *, reads: list[bytes] | None = None, fails: bool = False) -> None:
        self.bEndpointAddress = direction
        self.written: list[bytes] = []
        self._reads = reads or []
        self._fails = fails

    def read(self, size: int, timeout: int) -> bytes:
        if self._fails:
            raise ValueError("pipe stalled")
        return self._reads.pop(0) if self._reads else b""

    def write(self, data: bytes) -> None:
        self.written.append(data)


class _FakeUsbDevice:
    """A device as pyusb hands it over, down to the two endpoints."""

    IN, OUT = 0x81, 0x01

    def __init__(
        self,
        *,
        endpoints: tuple[int, ...] = (IN, OUT),
        kernel_driver: bool = False,
        detach_raises: bool = False,
        configure_raises: bool = False,
        serial: str = "DAQ-1",
    ) -> None:
        self.iSerialNumber = 3
        self.serial = serial
        self.detached = False
        self.configured = False
        self._endpoints = endpoints
        self._kernel_driver = kernel_driver
        self._detach_raises = detach_raises
        self._configure_raises = configure_raises

    def detach_kernel_driver(self, interface: int) -> None:
        if self._detach_raises:
            raise ValueError("busy")
        self.detached = True

    def get_active_configuration(self) -> dict[tuple[int, int], list[_FakeEndpoint]]:
        return {(0, 0): [_FakeEndpoint(address) for address in self._endpoints]}

    def is_kernel_driver_active(self, interface: int) -> bool:
        return self._kernel_driver

    def set_configuration(self) -> None:
        if self._configure_raises:
            raise ValueError("cannot configure")
        self.configured = True


# Stands in for the libusb backend handle, which the driver only checks for.
_A_BACKEND = object()


def _install_pyusb(
    monkeypatch: Any,
    *,
    devices: list[_FakeUsbDevice] | None = None,
    backend: Any = _A_BACKEND,
    backend_raises: bool = False,
    string_raises: bool = False,
) -> dict[str, Any]:
    """Put a stand-in for pyusb on `sys.modules`, as `open_usb` imports it.

    `open_usb` imports pyusb inside the call precisely so a host without it
    reports an unavailable instrument, which is also what makes it reachable
    from a test on a machine with no libusb.
    """
    import sys
    import types

    disposed: list[Any] = []

    usb = types.ModuleType("usb")
    core = types.ModuleType("usb.core")
    util = types.ModuleType("usb.util")
    libusb1 = types.ModuleType("usb.backend.libusb1")
    backends = types.ModuleType("usb.backend")

    def get_backend() -> Any:
        if backend_raises:
            raise ValueError("libusb missing")
        return backend

    def find(find_all: bool = False, **kwargs: Any) -> list[_FakeUsbDevice]:
        return list(devices or [])

    def get_string(device: Any, index: Any) -> str:
        if string_raises:
            raise ValueError("no descriptor")
        return device.serial

    util.ENDPOINT_IN = 0x80
    util.ENDPOINT_OUT = 0x00
    util.endpoint_direction = lambda address: address & 0x80
    util.find_descriptor = lambda interface, custom_match: next(
        (entry for entry in interface if custom_match(entry)), None
    )
    util.get_string = get_string
    util.dispose_resources = disposed.append
    core.find = find
    libusb1.get_backend = get_backend
    backends.libusb1 = libusb1
    usb.backend = backends
    usb.core = core
    usb.util = util

    for name, module in {
        "usb": usb,
        "usb.backend": backends,
        "usb.backend.libusb1": libusb1,
        "usb.core": core,
        "usb.util": util,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return {"disposed": disposed, "util": util}


class TestOpenUsb:
    """Claiming the device, which is the driver's only untestable-looking half."""

    def test_a_host_without_pyusb_reports_it(self, monkeypatch: Any) -> None:
        import sys

        for name in ("usb", "usb.core", "usb.util", "usb.backend", "usb.backend.libusb1"):
            monkeypatch.setitem(sys.modules, name, None)
        with pytest.raises(Di2008Error, match="pyusb is not importable"):
            open_usb()

    def test_a_backend_that_will_not_load_reports_it(self, monkeypatch: Any) -> None:
        _install_pyusb(monkeypatch, backend_raises=True)
        with pytest.raises(Di2008Error, match="libusb backend unusable"):
            open_usb()

    def test_no_backend_at_all_names_the_package_to_install(self, monkeypatch: Any) -> None:
        _install_pyusb(monkeypatch, backend=None)
        with pytest.raises(Di2008Error, match="install libusb"):
            open_usb()

    def test_an_empty_bus_is_reported(self, monkeypatch: Any) -> None:
        _install_pyusb(monkeypatch, devices=[])
        with pytest.raises(Di2008Error, match="no DI-2008 on the USB bus"):
            open_usb()

    def test_the_first_device_is_claimed_when_no_serial_is_asked_for(self, monkeypatch: Any) -> None:
        first, second = _FakeUsbDevice(serial="AAA"), _FakeUsbDevice(serial="BBB")
        _install_pyusb(monkeypatch, devices=[first, second])

        transport = open_usb()

        assert transport.serial_number() == "AAA"
        assert first.configured is True
        assert second.configured is False

    def test_a_serial_filter_picks_its_device(self, monkeypatch: Any) -> None:
        first, second = _FakeUsbDevice(serial="AAA"), _FakeUsbDevice(serial="BBB")
        _install_pyusb(monkeypatch, devices=[first, second])

        assert open_usb("BBB").serial_number() == "BBB"
        assert second.configured is True

    def test_a_serial_filter_that_matches_nothing_is_reported(self, monkeypatch: Any) -> None:
        _install_pyusb(monkeypatch, devices=[_FakeUsbDevice(serial="AAA")])
        with pytest.raises(Di2008Error, match="no DI-2008 with serial matching"):
            open_usb("ZZZ")

    def test_every_serial_on_the_bus_is_listed_in_order(self, monkeypatch: Any) -> None:
        _install_pyusb(monkeypatch, devices=[_FakeUsbDevice(serial="BBB"), _FakeUsbDevice(serial="AAA")])
        assert candidate_serials() == ["AAA", "BBB"]

    def test_listing_serials_claims_nothing(self, monkeypatch: Any) -> None:
        device = _FakeUsbDevice(serial="AAA")
        _install_pyusb(monkeypatch, devices=[device])
        candidate_serials()
        assert device.configured is False

    def test_an_empty_bus_lists_no_serials(self, monkeypatch: Any) -> None:
        _install_pyusb(monkeypatch, devices=[])
        assert candidate_serials() == []

    def test_a_kernel_driver_is_detached_first(self, monkeypatch: Any) -> None:
        device = _FakeUsbDevice(kernel_driver=True)
        _install_pyusb(monkeypatch, devices=[device])

        open_usb()

        assert device.detached is True

    def test_a_kernel_driver_that_will_not_detach_does_not_stop_the_claim(self, monkeypatch: Any) -> None:
        device = _FakeUsbDevice(kernel_driver=True, detach_raises=True)
        _install_pyusb(monkeypatch, devices=[device])

        assert open_usb().serial_number() == "DAQ-1"
        assert device.detached is False

    def test_a_device_that_will_not_configure_is_reported(self, monkeypatch: Any) -> None:
        _install_pyusb(monkeypatch, devices=[_FakeUsbDevice(configure_raises=True)])
        with pytest.raises(Di2008Error, match="cannot claim the DI-2008"):
            open_usb()

    def test_an_interface_without_both_endpoints_is_reported(self, monkeypatch: Any) -> None:
        _install_pyusb(monkeypatch, devices=[_FakeUsbDevice(endpoints=(_FakeUsbDevice.IN,))])
        with pytest.raises(Di2008Error, match="no bulk endpoint pair"):
            open_usb()

    def test_a_device_that_will_not_name_itself_reports_an_empty_serial(self, monkeypatch: Any) -> None:
        _install_pyusb(monkeypatch, devices=[_FakeUsbDevice()], string_raises=True)
        assert open_usb().serial_number() == ""


class TestLibusbTransport:
    """The endpoint pair, once claimed."""

    def _transport(self, monkeypatch: Any, **kwargs: Any) -> Any:
        _install_pyusb(monkeypatch, devices=[_FakeUsbDevice(**kwargs)])
        return open_usb()

    def test_a_write_goes_to_the_out_endpoint(self, monkeypatch: Any) -> None:
        transport = self._transport(monkeypatch)
        transport.write(b"stop\r")
        assert transport._endpoint_out.written == [b"stop\r"]

    def test_a_read_returns_the_bytes_waiting(self, monkeypatch: Any) -> None:
        transport = self._transport(monkeypatch)
        transport._endpoint_in._reads = [b"\x01\x02"]
        assert transport.read(64, 50) == b"\x01\x02"

    def test_a_read_that_times_out_is_empty_rather_than_an_error(self, monkeypatch: Any) -> None:
        transport = self._transport(monkeypatch)
        transport._endpoint_in._fails = True
        assert transport.read(64, 50) == b""

    def test_closing_releases_the_device(self, monkeypatch: Any) -> None:
        state = _install_pyusb(monkeypatch, devices=[_FakeUsbDevice()])
        transport = open_usb()

        transport.close()

        assert len(state["disposed"]) == 1

    def test_closing_survives_a_release_that_fails(self, monkeypatch: Any) -> None:
        state = _install_pyusb(monkeypatch, devices=[_FakeUsbDevice()])
        transport = open_usb()

        def explode(device: Any) -> None:
            raise ValueError("already gone")

        state["util"].dispose_resources = explode
        transport.close()  # does not raise


def _wait_for_scans(daq: Di2008Daq, count: int, seq: int = 1) -> Any:
    """The stream slice once it holds ``count`` scans from ``seq``, or fail."""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        streamed = daq.stream_since(seq, 10_000)
        if len(streamed.scans) >= count:
            return streamed
        time.sleep(0.005)
    raise AssertionError(f"fewer than {count} scans arrived")


class TestDi2008Stream:
    def _streaming(self, **options: Any) -> tuple[Di2008Daq, _FakeDaq]:
        clock = _Clock()
        transport = _FakeDaq(
            clock, stream=(tuple(range(at % 1000, at % 1000 + 8)) for at in itertools.count()), **options
        )
        return _daq(transport, clock), transport

    def test_a_lease_starts_the_scan_and_the_last_release_stops_it(self) -> None:
        daq, transport = self._streaming()
        assert daq.stream_open() is True
        assert daq.stream_open() is True
        assert transport.commands.count("start") == 1
        daq.stream_close()
        assert transport._scanning
        daq.stream_close()
        assert not transport._scanning

    def test_scans_are_numbered_from_one_with_a_value_per_channel(self) -> None:
        daq, _ = self._streaming()
        daq.stream_open()
        streamed = _wait_for_scans(daq, 5)
        daq.stream_close()
        assert [scan[0] for scan in streamed.scans[:5]] == [1, 2, 3, 4, 5]
        assert len(streamed.channels) == 8
        assert len(streamed.scans[0][3]) == 8
        assert streamed.scans[0][3][0] == value_from_code(0, "10v")
        assert streamed.scans[1][3][0] == value_from_code(1, "10v")

    def test_each_channel_says_the_range_it_can_read(self) -> None:
        daq, _ = self._streaming()
        daq.command("configure", {"rows": {"2": {"mode": "5v"}, "3": {"mode": "tc_k"}}})
        daq.stream_open()
        channels = {channel["key"]: channel for channel in daq.stream_since(1, 0).channels}
        daq.stream_close()
        assert (channels["1"]["min"], channels["1"]["max"]) == (-10.0, pytest.approx(10.0, abs=0.001))
        assert (channels["2"]["min"], channels["2"]["max"]) == (-5.0, pytest.approx(5.0, abs=0.001))
        assert channels["3"]["min"] == value_from_code(-32768, "tc_k")
        assert channels["3"]["max"] == value_from_code(32767, "tc_k")

    def test_every_channel_is_listed_with_whether_it_is_in_the_scan_list(self) -> None:
        daq, _ = self._streaming()
        daq.command("configure", {"rows": {"3": {"enabled": False}}})
        channels = daq.stream_channels()
        assert [channel["key"] for channel in channels] == [str(n) for n in range(1, 9)]
        assert {channel["key"]: channel["enabled"] for channel in channels}["3"] is False
        assert channels[0]["enabled"] is True
        assert (channels[0]["min"], channels[0]["unit"]) == (-10.0, "V")

    def test_channels_can_be_enabled_and_disabled_without_a_lease(self) -> None:
        daq, _ = self._streaming()
        daq.stream_enable({"2": False, "3": False})
        assert [channel["key"] for channel in daq.stream_since(1, 0).channels] == ["1", "4", "5", "6", "7", "8"]
        assert daq.state()["scan"]["rate_hz"] == pytest.approx(800 / (4 * 6))

    def test_the_last_enabled_channel_cannot_be_turned_off(self) -> None:
        daq, _ = self._streaming()
        with pytest.raises(CommandRejected, match="at least one"):
            daq.stream_enable({str(n): False for n in range(1, 9)})

    def test_what_is_recorded_but_not_shown_is_the_scan_and_the_enabled_flags(self) -> None:
        daq, _ = self._streaming()
        assert sorted(daq.trace_only()) == sorted(
            ["scan.auto", "scan.max_hz", "scan.rate_hz", *(f"channels.{n}.enabled" for n in range(1, 9))]
        )

    def test_the_rate_is_the_one_the_scan_list_gives(self) -> None:
        daq, _ = self._streaming()
        daq.stream_open()
        assert daq.stream_since(1, 1).rate_hz == pytest.approx(25.0)
        daq.stream_close()

    def test_since_returns_only_scans_from_that_number_on(self) -> None:
        daq, _ = self._streaming()
        daq.stream_open()
        _wait_for_scans(daq, 6)
        scans = daq.stream_since(4, 3).scans
        daq.stream_close()
        assert [scan[0] for scan in scans] == [4, 5, 6]

    def test_next_seq_follows_the_last_scan_returned(self) -> None:
        daq, _ = self._streaming()
        daq.stream_open()
        _wait_for_scans(daq, 3)
        streamed = daq.stream_since(1, 2)
        daq.stream_close()
        assert streamed.next_seq == 3

    def test_a_history_older_than_the_ring_is_clipped_to_its_oldest_scan(self) -> None:
        daq, _ = self._streaming()
        daq.stream_open()
        _wait_for_scans(daq, 5)
        daq.stream_close()
        daq._ring = type(daq._ring)(list(daq._ring)[-3:], maxlen=3)
        first = daq.stream_since(1, 10).scans[0][0]
        assert first == daq._ring[0][0]

    def test_sample_returns_the_newest_buffered_scan_without_restarting(self) -> None:
        daq, transport = self._streaming()
        daq.stream_open()
        _wait_for_scans(daq, 3)
        channels = daq.command("sample", {})["channels"]
        daq.stream_close()
        assert transport.commands.count("start") == 1
        assert len(channels) == 8

    def test_without_a_lease_sample_starts_and_stops_a_scan_as_before(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, tuple(range(8)))
        daq = _daq(transport, clock)
        daq.command("sample", {})
        assert "start" in transport.commands
        assert transport.commands[-1] == "stop"

    def test_configure_restarts_the_stream_and_the_numbers_keep_rising(self) -> None:
        daq, transport = self._streaming()
        daq.stream_open()
        before = _wait_for_scans(daq, 3).scans[-1][0]
        daq.command("configure", {"rows": {"3": {"mode": "tc_k"}}})
        after = _wait_for_scans(daq, 1, seq=before + 1).scans[0][0]
        daq.stream_close()
        assert transport.commands.count("start") == 2
        assert after > before

    def test_sample_after_configure_reads_the_restarted_stream_not_the_old_reading(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        daq, _ = self._streaming()
        daq.stream_open()
        _wait_for_scans(daq, 2)
        daq.command("sample", {})
        # With the reader held up, only a sample that reads the stream itself
        # can see the scan list the configure just loaded.
        monkeypatch.setattr(daq, "_stream_loop", lambda generation: threading.Event().wait(2.0))
        daq.command("configure", {"rows": {"2": {"enabled": False}}})
        channels = daq.command("sample", {})["channels"]
        daq.stream_close()
        assert channels["2"] is None
        assert channels["1"] is not None

    def test_scans_are_timed_by_the_unit_s_clock_not_by_when_they_were_read(self) -> None:
        daq, _ = self._streaming()
        daq.stream_open()
        scans = _wait_for_scans(daq, 6).scans
        daq.stream_close()
        spacing = [later[1] - earlier[1] for earlier, later in itertools.pairwise(scans)]
        wall = [later[2] - earlier[2] for earlier, later in itertools.pairwise(scans)]
        assert spacing == pytest.approx([1 / 25.0] * len(spacing))
        assert wall == pytest.approx([1 / 25.0] * len(wall), abs=1e-6)

    def test_a_slow_requested_rate_does_not_look_like_a_dead_stream(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, stream=(tuple(range(8)) for _ in itertools.count()), stream_every=40)
        daq = _daq(transport, clock)
        daq.command("scan_rate", {"rate": "0.5"})
        daq.stream_open()
        _wait_for_scans(daq, 3)
        reason = daq.describe()["unavailable_reason"]
        daq.stream_close()
        assert reason == ""

    def test_packets_of_a_slow_scan_are_minutes_apart_not_a_dead_stream(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, stream=(tuple(range(2)) for _ in itertools.count()), stream_every=160)
        daq = _daq(transport, clock)
        rows = {str(number): {"enabled": number < 3} for number in range(1, 9)}
        daq.command("configure", {"rows": rows})
        daq.command("scan_rate", {"rate": "0.5"})
        daq.stream_open()
        _wait_for_scans(daq, 2)
        reason = daq.describe()["unavailable_reason"]
        daq.stream_close()
        assert reason == ""

    def test_a_unit_that_goes_quiet_ends_the_stream_and_is_reported_unavailable(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, stream=iter([tuple(range(8))] * 3))
        daq = _daq(transport, clock)
        daq.stream_open()
        deadline = time.monotonic() + 5.0
        while daq._streaming() and time.monotonic() < deadline:
            time.sleep(0.005)
        assert daq.describe()["unavailable_reason"] == "the stream stopped delivering scans"
        daq.stream_close()

    def test_a_lease_cannot_be_taken_on_a_unit_that_is_not_there(self) -> None:
        def refuse(_: str) -> Any:
            raise Di2008Error("no DI-2008 on the USB bus")

        assert Di2008Daq(clock=_Clock(), open_transport=refuse).stream_open() is False


class TestDi2008ScanRate:
    def _daq(self) -> tuple[Di2008Daq, _FakeDaq]:
        clock = _Clock()
        transport = _FakeDaq(clock, tuple(range(8)))
        return _daq(transport, clock), transport

    @staticmethod
    def _only(daq: Di2008Daq, *keep: str) -> None:
        rows = {str(n): {"enabled": str(n) in keep} for n in range(1, 9)}
        daq.command("configure", {"rows": rows})

    def test_the_rate_is_auto_and_fastest_for_the_channels_enabled(self) -> None:
        daq, transport = self._daq()
        scan = daq.state()["scan"]
        assert (scan["auto"], scan["max_hz"], scan["rate_hz"]) == (True, 25.0, 25.0)
        self._only(daq, "1", "2", "3", "4")
        assert daq.state()["scan"]["rate_hz"] == pytest.approx(50.0)
        self._only(daq, "5")
        assert daq.state()["scan"]["rate_hz"] == pytest.approx(2000.0)
        assert "srate 4" in transport.commands

    def test_only_enabled_channels_are_in_the_scan_list(self) -> None:
        daq, transport = self._daq()
        self._only(daq, "2", "5")
        assert f"slist 0 {slist_word(1, '10v')}" in transport.commands
        assert f"slist 1 {slist_word(4, '10v')}" in transport.commands
        assert "slist 2 65535" in transport.commands
        assert list(transport._slots) == ["0", "1"]

    def test_a_disabled_channel_reads_nothing_and_the_others_keep_their_values(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock, (10, 20)), clock)
        self._only(daq, "2", "5")
        channels = daq.command("sample", {})["channels"]
        assert channels["2"] == value_from_code(10, "10v")
        assert channels["5"] == value_from_code(20, "10v")
        assert channels["1"] is None
        assert daq.state()["channels"]["1"]["enabled"] is False

    def test_a_slower_rate_is_set_by_srate_and_is_no_faster_than_asked(self) -> None:
        daq, transport = self._daq()
        self._only(daq, "1", "2", "3", "4")
        result = daq.command("scan_rate", {"rate": "20"})
        assert result["auto"] is False
        assert result["rate_hz"] == pytest.approx(800 / (10 * 4))
        assert "srate 10" in transport.commands
        assert daq.command("scan_rate", {"rate": "auto"})["rate_hz"] == pytest.approx(50.0)

    def test_the_rates_on_offer_follow_the_channels_and_name_the_one_chosen(self) -> None:
        daq, _ = self._daq()
        scan = daq.state()["scan"]
        assert scan["selected"] == "auto"
        assert scan["choices"][0] == "auto"
        assert scan["choices"][1] == "25"
        assert "12.5" in scan["choices"]
        self._only(daq, "1", "2", "3", "4")
        assert daq.state()["scan"]["choices"][1] == "50"
        daq.command("scan_rate", {"rate": "20"})
        assert daq.state()["scan"]["selected"] == "20"
        assert "20" in daq.state()["scan"]["choices"]

    def test_every_rate_on_offer_can_be_chosen(self) -> None:
        daq, _ = self._daq()
        for choice in daq.state()["scan"]["choices"]:
            result = daq.command("scan_rate", {"rate": choice})
            assert result["selected"] == choice
            assert result["rate_hz"] <= result["max_hz"] + 1e-9

    def test_the_scan_rate_is_offered_in_the_header_with_what_it_is_now(self) -> None:
        daq, _ = self._daq()
        command = next(entry for entry in daq.commands() if entry["name"] == "scan_rate")
        assert command["role"] == "header"
        assert (command["selected"], command["current"], command["unit"]) == ("scan.selected", "scan.rate_hz", "Hz")
        assert command["fields"][0]["choices_from"] == "scan.choices"

    def test_a_requested_rate_above_what_the_channels_allow_is_refused(self) -> None:
        daq, _ = self._daq()
        with pytest.raises(CommandRejected, match="between"):
            daq.command("scan_rate", {"rate": "26"})
        with pytest.raises(CommandRejected, match="between"):
            daq.command("scan_rate", {"rate": "fast"})

    def test_a_requested_rate_stays_when_channels_change_and_is_capped_by_them(self) -> None:
        daq, _ = self._daq()
        self._only(daq, "1", "2", "3", "4")
        daq.command("scan_rate", {"rate": "40"})
        assert daq.state()["scan"]["rate_hz"] == pytest.approx(40.0)
        self._only(daq, *map(str, range(1, 9)))
        scan = daq.state()["scan"]
        assert (scan["auto"], scan["max_hz"], scan["rate_hz"]) == (False, 25.0, 25.0)

    def test_the_last_channel_cannot_be_disabled(self) -> None:
        daq, _ = self._daq()
        self._only(daq, "1")
        with pytest.raises(CommandRejected, match="at least one"):
            daq.command("configure", {"rows": {"1": {"enabled": False}}})
        with pytest.raises(CommandRejected, match="true or false"):
            daq.command("configure", {"rows": {"2": {"enabled": "yes"}}})

    def test_the_stream_follows_the_enabled_channels_and_the_rate(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, stream=(tuple(range(at % 1000, at % 1000 + 2)) for at in itertools.count()))
        daq = _daq(transport, clock)
        self._only(daq, "3", "7")
        daq.stream_open()
        streamed = _wait_for_scans(daq, 2)
        daq.stream_close()
        assert [channel["key"] for channel in streamed.channels] == ["3", "7"]
        assert streamed.rate_hz == pytest.approx(100.0)
        assert len(streamed.scans[0][3]) == 2


class TestDi2008Failures:
    """What the DAQ does when the device stops behaving."""

    def test_a_transport_that_will_not_open_is_reported_not_raised(self) -> None:
        def refuse(_serial: str) -> Any:
            raise Di2008Error("no DI-2008 on the USB bus")

        daq = Di2008Daq(clock=_Clock(), open_transport=refuse)

        assert daq.available() is False
        assert "no DI-2008" in daq.describe()["unavailable_reason"]

    def test_a_device_that_answers_no_info_query_is_dropped(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock)
        transport.write = lambda data: None  # type: ignore[method-assign]
        daq = Di2008Daq(clock=clock, open_transport=lambda _: transport)

        assert daq.available() is False
        assert "did not answer" in daq.describe()["unavailable_reason"]
        assert transport.closed is True

    def test_a_command_on_an_unavailable_unit_is_refused_with_the_reason(self) -> None:
        def refuse(_serial: str) -> Any:
            raise Di2008Error("no DI-2008 on the USB bus")

        daq = Di2008Daq(clock=_Clock(), open_transport=refuse)
        with pytest.raises(CommandRejected, match="daq is unavailable"):
            daq.command("sample", {})

    def test_an_unknown_command_is_refused(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock, codes=(0,) * 8), clock)
        with pytest.raises(CommandRejected, match="no command 'launch'"):
            daq.command("launch", {})

    def test_an_acquisition_that_fails_mid_scan_drops_the_unit(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, codes=(0,) * 8)
        daq = _daq(transport, clock)
        assert daq.available() is True

        def explode(data: bytes) -> None:
            raise OSError("endpoint gone")

        transport.write = explode  # type: ignore[method-assign]
        reading = daq._acquire()

        assert reading == dict.fromkeys(daq._modes)
        assert "acquisition failed" in daq.describe()["unavailable_reason"]

    def test_a_window_with_no_complete_scan_keeps_the_last_reading(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, codes=(3276,) * 8, scans=1)
        daq = _daq(transport, clock)
        first = daq.command("sample", {})["channels"]
        assert first["1"] is not None

        transport._scans = 0
        again = daq._acquire()

        assert again == first

    def test_a_command_without_a_transport_says_so(self) -> None:
        daq = Di2008Daq(clock=_Clock(), open_transport=lambda _: _FakeDaq(_Clock()))
        with pytest.raises(Di2008Error, match="not connected"):
            daq._command("stop")

    def test_a_unit_that_will_not_stop_on_the_way_out_still_closes(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, codes=(0,) * 8)
        daq = _daq(transport, clock)
        assert daq.available() is True

        def explode(data: bytes) -> None:
            raise OSError("already unplugged")

        transport.write = explode  # type: ignore[method-assign]
        daq.close()

        assert transport.closed is True

    def test_the_usb_serial_stands_in_when_the_device_reports_none(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, codes=(0,) * 8)
        original = transport.write

        def blank_the_serial(data: bytes) -> None:
            if data == b"info 6\r":
                transport.commands.append("info 6")
                return
            original(data)

        transport.write = blank_the_serial  # type: ignore[method-assign]
        daq = _daq(transport, clock)

        assert daq.available() is True
        assert daq.describe()["serial"] == "USB-SERIAL"
        assert "USB-SERIAL" in daq.connection()


class TestDi2008Surface:
    """The members the panel and the capability API read."""

    def _live(self) -> tuple[Di2008Daq, _FakeDaq, _Clock]:
        clock = _Clock()
        transport = _FakeDaq(clock, codes=(3276,) * 8)
        daq = _daq(transport, clock)
        assert daq.available() is True
        return daq, transport, clock

    def test_it_reports_the_instance_the_suite_addresses(self) -> None:
        assert Di2008Daq(instance="daq7", open_transport=lambda _: _FakeDaq(_Clock())).instance_id() == "daq7"

    def test_sampling_is_the_command_the_panel_leads_with(self) -> None:
        daq, _transport, _clock = self._live()
        assert daq.primary_command() == "sample"

    def test_reading_the_capability_gives_the_same_state_the_panel_shows(self) -> None:
        daq, _transport, _clock = self._live()
        assert daq.read() == daq.state()

    def test_a_readout_is_declared_per_channel_and_no_more(self) -> None:
        daq, _transport, _clock = self._live()
        rows = daq.readouts()
        keys = [row["key"] for row in rows]
        assert keys == [f"channels.{n}.value" for n in range(1, 9)]
        # The mode is on the control that sets it, so it is not also a reading.
        assert not any(key.endswith(".mode") for key in keys)

    def test_a_readout_carries_the_unit_its_mode_reads_in(self) -> None:
        daq, _transport, _clock = self._live()
        daq.command("configure", {"rows": {"2": {"mode": "tc_k"}}})
        rows = {row["key"]: row for row in daq.readouts()}
        assert rows["channels.1.value"]["unit"] == "V"
        assert rows["channels.2.value"]["unit"] == "C"

    def test_writing_the_capability_runs_the_command_and_answers_with_the_state(self) -> None:
        daq, _transport, _clock = self._live()
        state = daq.write({"command": "configure", "args": {"rows": {"3": {"mode": "5v"}}}})
        assert state["channels"]["3"]["mode"] == "5v"

    def test_a_scan_list_with_no_channels_decodes_to_nothing(self) -> None:
        assert decode_scans(b"\x01\x02", 0) == []

    def test_an_empty_capture_has_no_echo_to_strip(self) -> None:
        assert strip_echo(b"") == b""


class TestHm310tFailures:
    """What the supply does when the port or the slave misbehaves."""

    def test_a_port_that_will_not_open_is_reported_not_raised(self) -> None:
        def refuse(_name: str) -> Any:
            raise OSError("permission denied")

        psu = Hm310tPsu("/dev/fake", clock=_Clock(), open_port=refuse)

        assert psu.available() is False
        assert "cannot open /dev/fake" in psu.describe()["unavailable_reason"]

    def test_a_slave_that_is_not_a_supply_is_rejected(self) -> None:
        supply = _FakeSupply(**{"0x0030": 60000})
        psu = _psu(supply)

        assert psu.available() is False
        assert "no supply answering" in psu.describe()["unavailable_reason"]
        assert supply.closed is True

    def test_a_command_on_an_unavailable_supply_is_refused_with_the_reason(self) -> None:
        def refuse(_name: str) -> Any:
            raise OSError("permission denied")

        psu = Hm310tPsu("/dev/fake", clock=_Clock(), open_port=refuse)
        with pytest.raises(CommandRejected, match="psu is unavailable"):
            psu.command("set_voltage", {"voltage": 1.0})

    def test_a_write_that_fails_is_refused_and_drops_the_port(self) -> None:
        supply = _FakeSupply()
        psu = _psu(supply)
        assert psu.available() is True

        def explode(data: bytes) -> int:
            raise OSError("cable pulled")

        supply.write = explode  # type: ignore[method-assign]
        with pytest.raises(CommandRejected, match="writing register 0x0030 failed"):
            psu.command("set_voltage", {"voltage": 5.0})
        assert "write of register 0x0030 failed" in psu.describe()["unavailable_reason"]

    def test_a_port_that_will_not_close_does_not_raise(self) -> None:
        supply = _FakeSupply()
        psu = _psu(supply)
        assert psu.available() is True

        def explode() -> None:
            raise OSError("already gone")

        supply.close = explode  # type: ignore[method-assign]
        psu.close()  # does not raise


class TestHm310tSurface:
    """The members the panel and the capability API read."""

    def test_it_names_the_port_and_its_line_settings(self) -> None:
        assert _psu(_FakeSupply()).connection() == "/dev/fake at 9600 8N1"

    def test_it_reports_the_instance_the_suite_addresses(self) -> None:
        psu = Hm310tPsu("/dev/fake", clock=_Clock(), instance="psu9", open_port=lambda _: _FakeSupply())
        assert psu.instance_id() == "psu9"

    def test_reading_the_capability_gives_the_same_state_the_panel_shows(self) -> None:
        psu = _psu(_FakeSupply())
        assert psu.read() == psu.state()

    def test_writing_the_capability_runs_the_command_and_answers_with_the_state(self) -> None:
        psu = _psu(_FakeSupply())
        state = psu.write({"command": "set_voltage", "args": {"voltage": 3.3}})
        assert state["voltage_setpoint"] == 3.3


class TestModbusReplies:
    """Frames the supply should never send, and what reading them says."""

    def _parse(self, frame: bytes, function: int = 0x03) -> Any:
        from gauntlet.instruments.hm310t_psu import _parse_response

        return _parse_response(frame, function)

    def _crc(self, body: bytes) -> bytes:
        return body + modbus_crc(body)

    def test_a_reply_too_short_to_carry_a_crc_is_refused(self) -> None:
        with pytest.raises(ModbusError, match="short reply"):
            self._parse(b"\x01\x03")

    def test_a_reply_from_another_slave_is_refused(self) -> None:
        with pytest.raises(ModbusError, match="reply from slave 9"):
            self._parse(self._crc(b"\x09\x03\x02\x00\x01"))

    def test_a_reply_for_another_function_is_refused(self) -> None:
        with pytest.raises(ModbusError, match="expected 0x03"):
            self._parse(self._crc(b"\x01\x04\x02\x00\x01"))

    def test_a_reply_whose_byte_count_does_not_match_is_refused(self) -> None:
        with pytest.raises(ModbusError, match="claims 4 bytes, carries 2"):
            self._parse(self._crc(b"\x01\x03\x04\x00\x01"))

    def test_a_silent_port_is_refused(self) -> None:
        from gauntlet.instruments.hm310t_psu import _read_frame

        supply = _FakeSupply()
        with pytest.raises(ModbusError, match="no reply"):
            _read_frame(supply)

    def test_a_reply_that_stops_before_its_byte_count_is_refused(self) -> None:
        from gauntlet.instruments.hm310t_psu import _read_frame

        class _Truncated:
            def __init__(self) -> None:
                self._pending = b"\x01\x03"

            def read(self, size: int) -> bytes:
                taken, self._pending = self._pending[:size], self._pending[size:]
                return taken

        with pytest.raises(ModbusError, match="ended before its byte count"):
            _read_frame(_Truncated())


class TestDetectionChoices:
    """Which driver detection builds, before any of them is registered."""

    def _settings(self, tmp_path: Any, **overrides: Any) -> Settings:
        return Settings(data_dir=tmp_path / "data", **{"daq_serial": "", "i2c_serial": "", "psu_port": "", **overrides})

    def test_a_named_daq_serial_is_taken_at_its_word(self, tmp_path: Any) -> None:
        """A serial names one unit, so it is built without probing the bus."""
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, daq_serial="DAQ-42"))

        daq = registry.provider("daq")
        assert daq is not None
        assert daq.describe()["driver"] == "di2008"

    def test_a_named_psu_port_is_taken_at_its_word(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, psu_port="/dev/ttyUSB9"))

        psu = registry.provider("psu")
        assert psu is not None
        assert psu.describe()["driver"] == "hm310t"

    def test_auto_drops_a_daq_that_does_not_answer(self, monkeypatch: Any, tmp_path: Any) -> None:
        from gauntlet.instruments import detect

        def refuse(_: str) -> Any:
            raise Di2008Error("no DI-2008 on the USB bus")

        # An empty bus, rather than whatever is plugged into the machine
        # running the tests: probing for real passes only where no DI-2008 is
        # attached, which is the one bench this suite most needs to pass on.
        monkeypatch.setattr(detect, "Di2008Daq", lambda **kwargs: Di2008Daq(open_transport=refuse, **kwargs))
        # And no NI module either, for the same reason: "auto" falls through to
        # NI-DAQmx, which on a bench that has the driver would answer for real.
        monkeypatch.setattr(detect, "NiDaqmxDaq", lambda **kwargs: NiDaqmxDaq(open_module=_no_module, **kwargs))
        # And no server on the host either, rather than whatever is listening
        # on the machine running the tests.
        monkeypatch.setattr(detect, "_listening", lambda _: False)
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, daq_serial="auto"))
        assert registry.provider("daq") is None

    def test_auto_finds_a_module_through_the_server_on_the_container_host(
        self, monkeypatch: Any, tmp_path: Any
    ) -> None:
        """A container has no local driver, so the host's server is the one it can reach."""
        from gauntlet.instruments import detect

        def refuse(_: str) -> Any:
            raise Di2008Error("no DI-2008 on the USB bus")

        def build(**kwargs: Any) -> Any:
            target = kwargs.get("target")
            opener = (lambda _: _NiModule()) if target is not None and target.server else _no_module
            return NiDaqmxDaq(open_module=opener, **kwargs)

        monkeypatch.setattr(detect, "Di2008Daq", lambda **kwargs: Di2008Daq(open_transport=refuse, **kwargs))
        monkeypatch.setattr(detect, "NiDaqmxDaq", build)
        monkeypatch.setattr(detect, "_listening", lambda _: True)
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, daq_serial="auto"))
        daq = registry.provider("daq")
        assert daq is not None
        assert daq.describe()["model"] == "NI 9238"
        assert detect._DAQMX_SERVER in daq.connection()

    def test_auto_does_not_build_a_server_provider_when_nothing_is_listening(
        self, monkeypatch: Any, tmp_path: Any
    ) -> None:
        """The address is probed before a provider is built, so a bench without one pays nothing."""
        from gauntlet.instruments import detect

        def refuse(_: str) -> Any:
            raise Di2008Error("no DI-2008 on the USB bus")

        targets: list[Any] = []

        def build(**kwargs: Any) -> Any:
            targets.append(kwargs.get("target"))
            return NiDaqmxDaq(open_module=_no_module, **kwargs)

        monkeypatch.setattr(detect, "Di2008Daq", lambda **kwargs: Di2008Daq(open_transport=refuse, **kwargs))
        monkeypatch.setattr(detect, "NiDaqmxDaq", build)
        monkeypatch.setattr(detect, "_listening", lambda _: False)
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, daq_serial="auto"))
        assert registry.provider("daq") is None
        assert [t for t in targets if t is not None and t.server] == []

    def test_a_closed_port_is_not_listening(self) -> None:
        """The probe answers for a port nothing holds rather than raising."""
        from gauntlet.instruments import detect

        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            port = taken.getsockname()[1]
        assert detect._listening(f"127.0.0.1:{port}") is False

    def test_auto_drops_a_psu_when_no_candidate_port_answers(self, monkeypatch: Any, tmp_path: Any) -> None:
        from gauntlet.instruments import detect

        monkeypatch.setattr(detect, "candidate_ports", lambda: ["/dev/ttyUSB8", "/dev/ttyUSB9"])
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, psu_port="auto"))
        assert registry.provider("psu") is None

    def test_a_named_i2c_serial_is_matched_from_the_candidates(self, monkeypatch: Any, tmp_path: Any) -> None:
        from gauntlet.instruments import detect

        monkeypatch.setattr(detect, "candidate_adapters", lambda: [("/dev/i2c-3", "CP-1"), ("/dev/i2c-4", "CP-2")])
        monkeypatch.setattr(cp2112.os, "access", lambda node, mode: True)
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, i2c_serial="CP-2", camera_device=""))
        bridge = registry.provider("i2c")
        assert bridge is not None
        assert bridge.connection() == "/dev/i2c-4 (I2C/SMBus)"

    def test_auto_drops_an_i2c_bridge_when_no_candidate_answers(self, monkeypatch: Any, tmp_path: Any) -> None:
        from gauntlet.instruments import detect

        monkeypatch.setattr(detect, "candidate_adapters", lambda: [("/dev/i2c-3", "CP-1")])
        monkeypatch.setattr(cp2112.os, "access", lambda node, mode: False)
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, i2c_serial="auto", camera_device=""))
        assert registry.provider("i2c") is None

    def test_auto_keeps_a_di2008_that_answers(self, monkeypatch: Any, tmp_path: Any) -> None:
        """A bench that has a DI-2008 keeps it, whatever else is attached beside it."""
        from gauntlet.instruments import detect

        clock = _Clock()
        monkeypatch.setattr(
            detect, "Di2008Daq", lambda **kwargs: Di2008Daq(clock=clock, open_transport=lambda _: _FakeDaq(clock))
        )
        monkeypatch.setattr(detect, "NiDaqmxDaq", lambda **kwargs: pytest.fail("NI-DAQmx must not be asked"))
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, daq_serial="auto"))
        daq = registry.provider("daq")
        assert daq is not None
        assert daq.describe()["driver"] == "di2008"

    def test_auto_registers_an_analyzer_that_is_on_the_bus(self, monkeypatch: Any, tmp_path: Any) -> None:
        from gauntlet.instruments import detect

        class _AttachedAnalyzer(Fx2Logic):
            def attached(self) -> bool:
                return True

        monkeypatch.setattr(detect, "Fx2Logic", _AttachedAnalyzer)
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, camera_device="", logic_serial="auto"))
        assert isinstance(registry.provider("logic"), _AttachedAnalyzer)

    def test_a_real_device_replaces_the_simulation_standing_in_for_it(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, simulated_instruments=["psu"]))
        assert is_simulated(registry.provider("psu")) is True

        detect_instruments(registry, self._settings(tmp_path, psu_port="/dev/ttyUSB9"))

        assert is_simulated(registry.provider("psu")) is False


class TestDi2008Quiet:
    """Paths a healthy unit never takes."""

    def test_the_commands_it_offers_name_every_channel_and_mode(self) -> None:
        daq = Di2008Daq(clock=_Clock(), open_transport=lambda _: _FakeDaq(_Clock()))
        commands = {entry["name"]: entry for entry in daq.commands()}

        assert set(commands) == {"configure", "sample", "scan_rate"}
        configure = commands["configure"]
        fields = {field["name"]: field for field in configure["fields"]}
        assert "tc_k" in fields["mode"]["choices"]
        # One row per channel, rather than a control that picks one.
        assert [row["key"] for row in configure["rows"]] == [str(n) for n in range(1, 9)]
        assert configure["row_label"] == "Channel"

    def test_acquiring_without_a_transport_answers_the_last_reading(self) -> None:
        daq = Di2008Daq(clock=_Clock(), open_transport=lambda _: _FakeDaq(_Clock()))
        assert daq._acquire() == dict.fromkeys(daq._modes)

    def test_draining_without_a_transport_reads_nothing(self) -> None:
        daq = Di2008Daq(clock=_Clock(), open_transport=lambda _: _FakeDaq(_Clock()))
        assert daq._drain() == b""

    def test_draining_collects_what_an_earlier_session_left_behind(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock, codes=(0,) * 8)
        daq = _daq(transport, clock)
        assert daq.available() is True

        transport._pending = b"leftover\r"
        assert daq._drain() == b"leftover\r"
