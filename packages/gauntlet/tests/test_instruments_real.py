"""The drivers for real instruments, and the choice between them and the mocks.

Every test here runs against a stand-in for the device, so none of them needs
hardware attached.
"""

from __future__ import annotations

import struct
from typing import Any

import pytest

from gauntlet.capabilities import CapabilityRegistry, CommandRejected
from gauntlet.config import Settings
from gauntlet.instruments import detect_instruments, is_simulated
from gauntlet.instruments.di2008_daq import (
    Di2008Daq,
    decode_scans,
    mode_unit,
    slist_word,
    strip_echo,
    value_from_code,
)
from gauntlet.instruments.hm310t_psu import (
    Hm310tPsu,
    ModbusError,
    modbus_crc,
    read_request,
    write_request,
)


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

    def __init__(self, clock: _Clock, codes: tuple[int, ...] = (), scans: int = 4) -> None:
        self.clock = clock
        self.closed = False
        self.commands: list[str] = []
        self._codes = codes
        self._pending = b""
        self._scanning = False
        self._scans = scans

    def close(self) -> None:
        self.closed = True

    def read(self, size: int, timeout_ms: int) -> bytes:
        # Every read costs time, so a capture loop bounded by the clock ends.
        self.clock.advance(0.05)
        if self._scanning and self._scans:
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
            self._pending += b"start\r"
        elif line == "stop":
            self._scanning = False
            self._pending = b""
        elif line.startswith("info "):
            self._pending += f"{line} {line.split()[1]}00\r".encode("ascii")


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
        # 0x0D is a legitimate high byte, so only the first 32 bytes are searched.
        payload = bytes(40) + b"\r"
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
        assert "dec 10" in transport.commands
        # Without this the device holds samples back until a packet is full.
        assert "ps 0" in transport.commands

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
        result = daq.command("set_mode", {"channel": "3", "mode": "tc_k"})
        assert result == {"channel": "3", "mode": "tc_k", "unit": "C"}
        assert f"slist 2 {slist_word(2, 'tc_k')}" in transport.commands
        assert daq.state()["channels"]["3"]["unit"] == "C"

    def test_a_thermocouple_channel_reads_in_celsius(self) -> None:
        clock = _Clock()
        codes = tuple(32 * 250 for _ in range(8))
        daq = _daq(_FakeDaq(clock, codes), clock)
        daq.command("set_mode", {"channel": "1", "mode": "tc_k"})
        assert daq.command("sample", {})["channels"]["1"] == 25.0

    def test_an_unknown_mode_is_refused(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock), clock)
        with pytest.raises(CommandRejected):
            daq.command("set_mode", {"channel": "1", "mode": "9000v"})

    def test_an_unknown_channel_is_refused(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock), clock)
        with pytest.raises(CommandRejected):
            daq.command("set_mode", {"channel": "99", "mode": "10v"})

    def test_state_is_all_none_while_nothing_answers(self) -> None:
        def refuse(_: str) -> Any:
            raise OSError("no DI-2008")

        daq = Di2008Daq(clock=_Clock(), open_transport=refuse)
        assert daq.available() is False
        values = [c["value"] for c in daq.state()["channels"].values()]
        assert values == [None] * 8
        assert "no DI-2008" in daq.describe()["unavailable_reason"]

    def test_the_scan_rate_divides_the_base_clock(self) -> None:
        clock = _Clock()
        daq = Di2008Daq(clock=clock, dec=10, srate=4, open_transport=lambda _: _FakeDaq(clock))
        # 8000 / (4 * 10) across eight channels.
        assert daq.scan_rate_hz() == 25.0

    def test_identity_comes_from_the_device(self) -> None:
        clock = _Clock()
        daq = _daq(_FakeDaq(clock), clock)
        assert daq.available()
        assert daq.describe()["serial"] == "600"
        assert "0683:2008" in daq.connection()

    def test_closing_stops_the_scan_and_releases_the_device(self) -> None:
        clock = _Clock()
        transport = _FakeDaq(clock)
        daq = _daq(transport, clock)
        assert daq.available()
        daq.close()
        assert transport.closed is True
        assert transport.commands[-1] == "stop"


class TestDetection:
    def _settings(self, tmp_path: Any, **overrides: Any) -> Settings:
        """Settings that look for nothing, bar what a test asks for."""
        return Settings(data_dir=tmp_path / "data", **{"daq_serial": "", "psu_port": "", **overrides})

    def test_nothing_answering_registers_nothing(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path))
        assert registry.names() == []

    def test_a_simulation_is_registered_only_when_it_is_named(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, simulated_instruments=["psu"]))
        assert registry.names() == ["psu"]
        assert is_simulated(registry.provider("psu")) is True

    def test_the_chamber_exists_only_while_it_is_simulated(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, simulated_instruments=["chamber"]))
        assert registry.names() == ["chamber"]
        # There is no driver for a real chamber, so dropping the simulation
        # leaves nothing behind.
        detect_instruments(registry, self._settings(tmp_path))
        assert registry.names() == []

    def test_a_named_port_registers_the_driver_even_when_it_is_silent(self, tmp_path: Any) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, self._settings(tmp_path, psu_port="/dev/does-not-exist"))
        psu = registry.provider("psu")
        assert is_simulated(psu) is False
        assert psu.available() is False
        assert "/dev/does-not-exist" in psu.describe()["unavailable_reason"]

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
