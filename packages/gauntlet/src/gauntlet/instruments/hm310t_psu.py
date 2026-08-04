"""Hanmatek HM310T bench supply, over Modbus RTU on a serial port.

The device is a single-channel 30 V / 10 A supply speaking Modbus RTU at
9600 8N1 as slave 1. Only two function codes are needed — read holding
registers (0x03) and write single register (0x06) — so the framing is inline
here rather than pulled in as a dependency.

Holding registers this driver uses:

- ``0x0001`` output enable, 1 = on
- ``0x0010`` display voltage, centivolts
- ``0x0011`` display current, milliamps
- ``0x0030`` set voltage, centivolts
- ``0x0031`` set current, milliamps

Power is computed host-side as ``V x I``. The device splits display power
across ``0x0012`` and ``0x0013`` with firmware-dependent scaling, and the low
register answers with a Modbus exception while the output is off.

A failed read leaves the corresponding state value ``None`` rather than
raising, so one flaky exchange cannot abort a long run.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import serial
from serial.tools import list_ports

from gauntlet.capabilities.declare import command_field, number_arg, readout
from gauntlet.capabilities.registry import CommandRejected

log = logging.getLogger("gauntlet.instruments.hm310t")

_BAUD = 9600
_SLAVE = 1
_TIMEOUT_S = 0.5

_FUNCTION_READ = 0x03
_FUNCTION_WRITE = 0x06

_REGISTER_OUTPUT_ENABLE = 0x0001
_REGISTER_DISPLAY_VOLTAGE = 0x0010
_REGISTER_DISPLAY_CURRENT = 0x0011
_REGISTER_SET_VOLTAGE = 0x0030
_REGISTER_SET_CURRENT = 0x0031

_MAX_AMPS = 10.0
_MAX_VOLTS = 30.0

# USB-to-serial bridges a bench supply is found behind. An automatic probe
# only opens ports matching one of these, so it cannot write Modbus frames at
# an unrelated device that happens to expose a serial node. Configuring a port
# explicitly bypasses the filter.
_USB_SERIAL_BRIDGES = (
    (0x0403, 0x6001),  # FTDI FT232R
    (0x067B, 0x2303),  # Prolific PL2303
    (0x10C4, 0xEA60),  # Silicon Labs CP210x
    (0x1A86, 0x7523),  # WCH CH340
)


class ModbusError(RuntimeError):
    """An exchange failed, or the device answered with a Modbus exception."""


class SerialPort(Protocol):
    """The part of ``serial.Serial`` this driver uses."""

    def close(self) -> None: ...

    def read(self, size: int) -> bytes: ...

    def reset_input_buffer(self) -> None: ...

    def write(self, data: bytes) -> int | None: ...


def modbus_crc(frame: bytes) -> bytes:
    """CRC-16/MODBUS over ``frame``, low byte first as RTU appends it."""
    crc = 0xFFFF
    for byte in frame:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return bytes((crc & 0xFF, crc >> 8))


def read_request(address: int, count: int = 1, *, slave: int = _SLAVE) -> bytes:
    """A read-holding-registers frame, ready to put on the wire."""
    body = bytes((slave, _FUNCTION_READ, address >> 8, address & 0xFF, count >> 8, count & 0xFF))
    return body + modbus_crc(body)


def write_request(address: int, value: int, *, slave: int = _SLAVE) -> bytes:
    """A write-single-register frame, ready to put on the wire."""
    body = bytes((slave, _FUNCTION_WRITE, address >> 8, address & 0xFF, value >> 8, value & 0xFF))
    return body + modbus_crc(body)


def open_serial(port: str) -> SerialPort:
    """Open a port with the settings the HM310T expects."""
    return serial.Serial(
        port=port,
        baudrate=_BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=_TIMEOUT_S,
        write_timeout=_TIMEOUT_S,
    )


def candidate_ports() -> list[str]:
    """Serial ports worth probing for a supply, in the order to try them."""
    return [port.device for port in list_ports.comports() if (port.vid, port.pid) in _USB_SERIAL_BRIDGES]


class Hm310tPsu:
    """Capability provider backed by a real HM310T.

    Registered under the same name as the simulated supply, so a bench with
    hardware attached gets the same panel and the same command names.
    """

    name = "psu"

    def __init__(
        self,
        port: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        instance: str = "psu0",
        open_port: Callable[[str], SerialPort] = open_serial,
        probe_interval_s: float = 3.0,
    ) -> None:
        self._clock = clock
        self._instance = instance
        self._lock = threading.Lock()
        self._open_port = open_port
        self._port_name = port
        self._probe_interval_s = probe_interval_s
        self._serial: SerialPort | None = None
        # Far enough in the past that the first probe happens immediately.
        self._last_probe = clock() - probe_interval_s
        self._unavailable_reason = "not yet probed"

    def available(self) -> bool:
        """Is the supply answering right now.

        Polled by the UI on every refresh, so a disconnected supply is
        re-probed at most once per ``probe_interval_s`` and the answer between
        probes is the cached one.
        """
        with self._lock:
            return self._connect()

    def close(self) -> None:
        """Drop the serial port, leaving the output exactly as it is.

        Detection calls this when it replaces a provider. Switching the output
        off here would cut power to whatever a run is driving, so it does not.
        """
        with self._lock:
            self._disconnect()

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command and return what it changed."""
        if name not in {"set_current_limit", "set_output", "set_voltage"}:
            raise CommandRejected(f"psu has no command {name!r}")
        with self._lock:
            if not self._connect():
                raise CommandRejected(f"psu is unavailable: {self._unavailable_reason}")
            if name == "set_voltage":
                volts = number_arg("psu", args, "voltage", 0.0, _MAX_VOLTS)
                self._write_register(_REGISTER_SET_VOLTAGE, round(volts * 100))
                return {"voltage_setpoint": volts}
            if name == "set_current_limit":
                amps = number_arg("psu", args, "current", 0.0, _MAX_AMPS)
                self._write_register(_REGISTER_SET_CURRENT, round(amps * 1000))
                return {"current_limit": amps}
            enabled = args.get("enabled")
            if not isinstance(enabled, bool):
                raise CommandRejected("psu: 'enabled' must be true or false")
            self._write_register(_REGISTER_OUTPUT_ENABLE, int(enabled))
            return {"output_enabled": enabled}

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers."""
        return [
            {
                "name": "set_voltage",
                "label": "Set Voltage",
                "fields": [command_field("voltage", "Voltage", unit="V", minimum=0.0, maximum=_MAX_VOLTS)],
            },
            {
                "name": "set_current_limit",
                "label": "Set Current Limit",
                "fields": [command_field("current", "Current", unit="A", minimum=0.0, maximum=_MAX_AMPS)],
            },
            {
                "danger": True,
                "name": "set_output",
                "label": "Set Output",
                "fields": [command_field("enabled", "Enabled", "boolean")],
            },
        ]

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""
        return f"{self._port_name} at {_BAUD} 8N1"

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "Single-channel bench supply with readback and current limiting.",
            "driver": "hm310t",
            "kind": "psu",
            "model": "HM310T",
            "unavailable_reason": self._unavailable_reason,
        }

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""
        return self._instance

    def primary_command(self) -> str:
        """Enabling the output energises the rail, so it gets the full width."""
        return "set_output"

    def read(self) -> dict[str, Any]:
        """Current state, for suites driving the capability API."""
        return self.state()

    def readouts(self) -> list[dict[str, Any]]:
        """Readback as tiles, with the setpoints beneath them."""
        return [
            readout("voltage", "Voltage", precision=2, unit="V"),
            readout("current", "Current", precision=3, unit="A"),
            readout("power", "Power", precision=2, unit="W"),
            readout("voltage_setpoint", "Set V", precision=1, role="summary", unit="V"),
            readout("current_limit", "Limit I", precision=1, role="summary", unit="A"),
            readout("output_enabled", "Output", role="summary"),
        ]

    def state(self) -> dict[str, Any]:
        """Everything the supply reports, as of now.

        Every value is ``None`` while the supply is unreachable, and any single
        register that fails to read is ``None`` on its own.
        """
        with self._lock:
            if not self._connect():
                return _empty_state()
            # The two display registers are adjacent, as are the two
            # setpoints, so three exchanges cover all five values.
            enabled = self._read_registers(_REGISTER_OUTPUT_ENABLE, 1)
            display = self._read_registers(_REGISTER_DISPLAY_VOLTAGE, 2)
            setpoints = self._read_registers(_REGISTER_SET_VOLTAGE, 2)
        voltage = _scaled(display, 0, 0.01)
        current = _scaled(display, 1, 0.001)
        return {
            "current": current,
            "current_limit": _scaled(setpoints, 1, 0.001),
            "output_enabled": None if enabled is None else bool(enabled[0]),
            "power": None if voltage is None or current is None else round(voltage * current, 3),
            "voltage": voltage,
            "voltage_setpoint": _scaled(setpoints, 0, 0.01),
        }

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Run a command given as ``{"command": ..., "args": {...}}``."""
        self.command(str(values.get("command", "")), dict(values.get("args") or {}))
        return self.state()

    def _connect(self) -> bool:
        """Open the port if it is not already open, at most once per interval."""
        if self._serial is not None:
            return True
        now = self._clock()
        if now - self._last_probe < self._probe_interval_s:
            return False
        self._last_probe = now
        try:
            port = self._open_port(self._port_name)
        except (OSError, ValueError) as exc:
            self._unavailable_reason = f"cannot open {self._port_name}: {exc}"
            return False
        self._serial = port
        try:
            self._identify()
        except (ModbusError, OSError) as exc:
            self._unavailable_reason = f"no supply answering on {self._port_name}: {exc}"
            self._disconnect()
            return False
        self._unavailable_reason = ""
        return True

    def _disconnect(self) -> None:
        port, self._serial = self._serial, None
        if port is None:
            return
        try:
            port.close()
        except OSError as exc:
            log.debug("closing %s: %s", self._port_name, exc)

    def _identify(self) -> None:
        """Confirm the thing on the port is a supply, not some other slave.

        A valid CRC already proves something answers Modbus at this address;
        the range check is a loose second opinion, wide enough to accept any
        setpoint a supply in this family could be holding.
        """
        registers = self._transact(read_request(_REGISTER_SET_VOLTAGE), _FUNCTION_READ)
        if not 0 <= registers[0] <= 12000:
            raise ModbusError(f"implausible set voltage {registers[0]} centivolts")

    def _read_registers(self, address: int, count: int) -> list[int] | None:
        """Read ``count`` registers, or ``None`` if the exchange fails."""
        try:
            return self._transact(read_request(address, count), _FUNCTION_READ)
        except (ModbusError, OSError) as exc:
            log.debug("read 0x%04X failed: %s", address, exc)
            self._unavailable_reason = f"read of register 0x{address:04X} failed: {exc}"
            self._disconnect()
            return None

    def _transact(self, request: bytes, function: int) -> list[int]:
        """Send one frame and return the registers in the reply."""
        port = self._serial
        if port is None:
            raise ModbusError("not connected")
        port.reset_input_buffer()
        port.write(request)
        return _parse_response(_read_frame(port), function)

    def _write_register(self, address: int, value: int) -> None:
        try:
            self._transact(write_request(address, value), _FUNCTION_WRITE)
        except (ModbusError, OSError) as exc:
            self._unavailable_reason = f"write of register 0x{address:04X} failed: {exc}"
            self._disconnect()
            raise CommandRejected(f"psu: writing register 0x{address:04X} failed: {exc}") from exc


def _empty_state() -> dict[str, Any]:
    """The shape ``state`` returns while nothing is answering."""
    return {
        "current": None,
        "current_limit": None,
        "output_enabled": None,
        "power": None,
        "voltage": None,
        "voltage_setpoint": None,
    }


def _parse_response(frame: bytes, function: int) -> list[int]:
    """Check a reply's CRC and function, and pull its registers out.

    A write reply echoes the address and the value written, which this
    returns in the same shape as a read of one register.
    """
    if len(frame) < 5:
        raise ModbusError(f"short reply: {frame.hex()}")
    if modbus_crc(frame[:-2]) != frame[-2:]:
        raise ModbusError(f"bad CRC on reply: {frame.hex()}")
    if frame[0] != _SLAVE:
        raise ModbusError(f"reply from slave {frame[0]}, expected {_SLAVE}")
    if frame[1] == function | 0x80:
        raise ModbusError(f"device reported exception {frame[2]}")
    if frame[1] != function:
        raise ModbusError(f"reply for function 0x{frame[1]:02X}, expected 0x{function:02X}")
    if function == _FUNCTION_WRITE:
        return [int.from_bytes(frame[4:6], "big")]
    payload = frame[3:-2]
    if len(payload) != frame[2]:
        raise ModbusError(f"reply claims {frame[2]} bytes, carries {len(payload)}")
    return [int.from_bytes(payload[at : at + 2], "big") for at in range(0, len(payload), 2)]


def _read_frame(port: SerialPort) -> bytes:
    """Read one reply, using its function code to know how long it is.

    Reading the length rather than waiting out the timeout keeps an exception
    reply, which is shorter than the read it answers, from costing a stall.
    """
    head = port.read(2)
    if len(head) < 2:
        raise ModbusError("no reply")
    function = head[1]
    if function & 0x80:
        return head + port.read(3)
    if function == _FUNCTION_WRITE:
        return head + port.read(6)
    count = port.read(1)
    if len(count) < 1:
        raise ModbusError("reply ended before its byte count")
    return head + count + port.read(count[0] + 2)


def _scaled(registers: list[int] | None, index: int, scale: float) -> float | None:
    """One register as the unit an operator reads, or ``None`` if it is missing."""
    if registers is None or index >= len(registers):
        return None
    return round(registers[index] * scale, 4)
