"""DATAQ DI-2008 acquisition unit, over its vendor bulk-USB protocol.

Eight analog inputs, each independently either a voltage range or a
thermocouple type. The host writes ASCII commands terminated by ``\\r`` to the
bulk-OUT endpoint and reads back either an ASCII echo or, while scanning, a
stream of little-endian signed 16-bit samples on bulk-IN.

Protocol detail that does not read off the datasheet:

- ``ps 0`` must precede ``start``, or the device holds samples back until it
  has a full 64-byte packet.
- A scan list entry is ``slist <slot> <word>``, where the word is
  ``(mode << 8) | channel`` and the channel is 0-based. The device takes at
  most 11 entries.
- The sample stream begins at the first slot of the scan list, so the endpoint
  is drained immediately before ``start``. A stale sample left in it would
  otherwise be read as the first channel and rotate every reading onto the
  wrong one.
- A leftover scan from an earlier session prefixes the first reply, so the
  driver sends ``stop`` and drains the endpoint as it connects.

Aggregate scan rate is ``clock / (srate * dec)`` Hz shared across the whole
scan list, and ``info 9`` is the clock. It is not the fixed 8 kHz the base
clock suggests: a scan list of one channel runs at 8000 Hz, and any longer
list at 800 Hz. So the clock is read back after the list is loaded rather than
assumed, which is what makes the rate this driver reports the rate the device
actually delivers.
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from gauntlet.capabilities.declare import command_field, command_row, readout
from gauntlet.capabilities.registry import CommandRejected

log = logging.getLogger("gauntlet.instruments.di2008")

PRODUCT_ID = 0x2008
VENDOR_ID = 0x0683

_ANALOG_COUNT = 8
_BASE_CLOCK_HZ = 8000
_PACKET_BYTES = 64
_START_ECHO = b"start\r"

# Scans to capture before keeping the last one, and the longest a capture may
# run whatever the configured rate. Two scans mean a reading is never the one
# in progress when the capture opened.
_CAPTURE_SCANS = 2
_CAPTURE_LIMIT_S = 2.0

# Longest channel label kept, in characters. Enough to name what is wired to a
# channel, short enough to sit under the reading without crowding its neighbours.
_MAX_LABEL = 32

# Voltage ranges: mode name -> (slist mode code, full scale in volts). A code
# maps linearly onto the signed 16-bit range, so volts = code * scale / 32768.
_VOLTAGE_MODES: dict[str, tuple[int, float]] = {
    "10v": (0x08, 10.0),
    "5v": (0x09, 5.0),
    "2.5v": (0x0A, 2.5),
    "1v": (0x0B, 1.0),
    "500mv": (0x0C, 0.5),
    "250mv": (0x0D, 0.25),
    "100mv": (0x0E, 0.1),
    "50mv": (0x0F, 0.05),
    "25mv": (0x10, 0.025),
}

# Thermocouple types. The device converts on board and sends 0.1 C in 32-tick
# steps, so the full scale above does not apply.
_THERMOCOUPLE_MODES: dict[str, int] = {
    "tc_b": 0x00,
    "tc_e": 0x01,
    "tc_j": 0x02,
    "tc_k": 0x03,
    "tc_n": 0x04,
    "tc_r": 0x05,
    "tc_s": 0x06,
    "tc_t": 0x07,
}

MODES: tuple[str, ...] = tuple(_VOLTAGE_MODES) + tuple(_THERMOCOUPLE_MODES)

_INFO_VENDOR = 0
_INFO_PRODUCT = 1
_INFO_FIRMWARE = 2
_INFO_SERIAL = 6
_INFO_CLOCK = 9


class Di2008Error(RuntimeError):
    """The unit could not be reached, or answered with something unusable."""


class UsbTransport(Protocol):
    """The bulk endpoints of one DI-2008, as this driver uses them."""

    def close(self) -> None: ...

    def read(self, size: int, timeout_ms: int) -> bytes: ...

    def serial_number(self) -> str: ...

    def write(self, data: bytes) -> None: ...


def mode_unit(mode: str) -> str:
    """The unit a channel in ``mode`` reads in."""
    return "C" if mode in _THERMOCOUPLE_MODES else "V"


def slist_word(channel: int, mode: str) -> int:
    """The scan-list word selecting ``mode`` on a zero-based ``channel``."""
    if mode in _VOLTAGE_MODES:
        code = _VOLTAGE_MODES[mode][0]
    elif mode in _THERMOCOUPLE_MODES:
        code = _THERMOCOUPLE_MODES[mode]
    else:
        raise ValueError(f"unknown mode {mode!r}")
    return (code << 8) | channel


def value_from_code(code: int, mode: str) -> float:
    """One raw sample as the unit its mode reads in."""
    if mode in _THERMOCOUPLE_MODES:
        return round(code * 0.1 / 32.0, 4)
    return round(code * _VOLTAGE_MODES[mode][1] / 32768.0, 6)


def decode_scans(payload: bytes, channel_count: int) -> list[tuple[int, ...]]:
    """Split a captured stream into the raw codes of each complete scan.

    A trailing partial scan is dropped, so every tuple holds one code per
    channel in scan-list order.
    """
    if channel_count <= 0:
        return []
    per_scan = 2 * channel_count
    scans = len(payload) // per_scan
    if not scans:
        return []
    codes = struct.unpack(f"<{scans * channel_count}h", payload[: scans * per_scan])
    return [codes[at : at + channel_count] for at in range(0, len(codes), channel_count)]


def strip_echo(buf: bytes) -> bytes:
    """Drop the ``start`` echo, for a unit that sends one before its samples.

    Only that exact prefix is removed. Searching the head of the capture for a
    terminator instead would cut the stream at the first sample whose low byte
    happens to be ``0x0D`` — a reading of 0.004 V on the widest range — and
    shift every channel onto its neighbour's value.
    """
    return buf[len(_START_ECHO) :] if buf.startswith(_START_ECHO) else buf


def open_usb(serial_filter: str = "") -> UsbTransport:
    """Claim the first DI-2008 on the bus, or one matching ``serial_filter``.

    pyusb is imported here rather than at module scope so that a host without
    a usable libusb reports an unavailable instrument instead of failing to
    start.
    """
    try:
        import usb.backend.libusb1
        import usb.core
        import usb.util
    except ImportError as exc:
        raise Di2008Error(f"pyusb is not importable: {exc}") from exc

    try:
        backend = usb.backend.libusb1.get_backend()
    except Exception as exc:
        raise Di2008Error(f"libusb backend unusable: {exc}") from exc
    if backend is None:
        raise Di2008Error("no libusb backend: install libusb-1.0-0")

    found = list(usb.core.find(find_all=True, idVendor=VENDOR_ID, idProduct=PRODUCT_ID) or [])
    if not found:
        raise Di2008Error("no DI-2008 on the USB bus")

    device = None
    if serial_filter:
        for candidate in found:
            if serial_filter in _usb_string(usb.util, candidate, candidate.iSerialNumber):
                device = candidate
                break
        if device is None:
            raise Di2008Error(f"no DI-2008 with serial matching {serial_filter!r}")
    else:
        device = found[0]

    try:
        if device.is_kernel_driver_active(0):
            device.detach_kernel_driver(0)
    except Exception as exc:
        log.debug("no kernel driver to detach: %s", exc)
    try:
        device.set_configuration()
        interface = device.get_active_configuration()[(0, 0)]
        endpoint_in = usb.util.find_descriptor(
            interface,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN,
        )
        endpoint_out = usb.util.find_descriptor(
            interface,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT,
        )
    except Exception as exc:
        raise Di2008Error(f"cannot claim the DI-2008: {exc}") from exc
    if endpoint_in is None or endpoint_out is None:
        raise Di2008Error("the DI-2008 interface has no bulk endpoint pair")
    return _LibusbTransport(device, endpoint_in, endpoint_out, _usb_string(usb.util, device, device.iSerialNumber))


class _LibusbTransport:
    """A claimed DI-2008, reduced to the two endpoints the driver writes to."""

    def __init__(self, device: Any, endpoint_in: Any, endpoint_out: Any, serial: str) -> None:
        self._device = device
        self._endpoint_in = endpoint_in
        self._endpoint_out = endpoint_out
        self._serial = serial

    def close(self) -> None:
        try:
            import usb.util

            usb.util.dispose_resources(self._device)
        except Exception as exc:
            log.debug("releasing the DI-2008: %s", exc)

    def read(self, size: int, timeout_ms: int) -> bytes:
        """Whatever is waiting on bulk-IN, empty when the read times out."""
        try:
            return bytes(self._endpoint_in.read(size, timeout=timeout_ms))
        except Exception:
            return b""

    def serial_number(self) -> str:
        return self._serial

    def write(self, data: bytes) -> None:
        self._endpoint_out.write(data)


class Di2008Daq:
    """Capability provider backed by a real DI-2008.

    Registered under the same name as the simulated unit, so a bench with
    hardware attached gets the same panel and the same command names.
    """

    name = "daq"

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        dec: int = 1,
        instance: str = "daq0",
        open_transport: Callable[[str], UsbTransport] = open_usb,
        probe_interval_s: float = 3.0,
        sample_interval_s: float = 1.0,
        serial_filter: str = "",
        srate: int = 4,
    ) -> None:
        self._clock = clock
        self._dec = dec
        self._instance = instance
        self._lock = threading.RLock()
        self._modes = {str(number): "10v" for number in range(1, _ANALOG_COUNT + 1)}
        # What is wired to each channel, once someone says. Empty until then,
        # which is what makes a reading fall back to its channel number.
        self._labels = dict.fromkeys(self._modes, "")
        self._open_transport = open_transport
        self._probe_interval_s = probe_interval_s
        self._sample_interval_s = sample_interval_s
        self._serial_filter = serial_filter
        self._srate = srate
        self._transport: UsbTransport | None = None
        # Replaced by what the device reports once a scan list is loaded.
        self._clock_hz = float(_BASE_CLOCK_HZ)
        self._identity: dict[str, str] = {}
        self._reading: dict[str, float | None] = dict.fromkeys(self._modes)
        # Far enough in the past that the first probe and the first sample
        # both happen immediately.
        self._last_probe = clock() - probe_interval_s
        self._last_sample = clock() - sample_interval_s
        self._unavailable_reason = "not yet probed"

    def available(self) -> bool:
        """Is the unit answering right now.

        Polled by the UI on every refresh, so a disconnected unit is re-probed
        at most once per ``probe_interval_s`` and the answer between probes is
        the cached one.
        """
        with self._lock:
            return self._connect()

    def close(self) -> None:
        """Halt any scan in progress and release the USB device."""
        with self._lock:
            self._disconnect()

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command and return what it produced."""
        with self._lock:
            if not self._connect():
                raise CommandRejected(f"daq is unavailable: {self._unavailable_reason}")
            if name == "configure":
                return self._configure_channels(args)
            if name == "sample":
                return {"channels": self._acquire()}
            raise CommandRejected(f"daq has no command {name!r}")

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers.

        One row per channel rather than a control that picks a channel and a
        control that sets it: eight channels are configured in one pass, and
        the scan list is reloaded once for the lot instead of once per change.
        """
        with self._lock:
            rows = [
                command_row(name, f"CH {name}", {"label": self._labels[name], "mode": mode})
                for name, mode in self._modes.items()
            ]
        return [
            {
                "name": "configure",
                "label": "Apply",
                "row_label": "Channel",
                "rows": rows,
                "fields": [
                    command_field("mode", "Mode", "string", choices=MODES),
                    command_field("label", "Label", "string"),
                ],
            },
            {"name": "sample", "label": "Sample", "fields": []},
        ]

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""
        serial = self._identity.get("serial", "")
        return f"USB {VENDOR_ID:04x}:{PRODUCT_ID:04x}" + (f" serial {serial}" if serial else "")

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "Eight-channel analog acquisition, per channel a voltage range or a thermocouple.",
            "driver": "di2008",
            "firmware": self._identity.get("firmware", ""),
            "kind": "daq",
            "model": "DI-2008",
            "serial": self._identity.get("serial", ""),
            "unavailable_reason": self._unavailable_reason,
        }

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""
        return self._instance

    def primary_command(self) -> str:
        """Taking one scan is what an operator comes to this panel for."""
        return "sample"

    def read(self) -> dict[str, Any]:
        """Current state, for suites driving the capability API."""
        return self.state()

    def readouts(self) -> list[dict[str, Any]]:
        """One reading per analog input.

        Each is named for whatever the operator or the suite said is wired to
        that channel, so the panel, the dashboard tile and the chart legend all
        read the same way without knowing a label from a channel number.

        The modes are not among them. Every channel carries one, and the
        ``configure`` command already shows each on the control that sets it,
        so declaring them here would spend a second row of the display saying
        what the first row of the table beneath says.
        """
        with self._lock:
            modes = dict(self._modes)
            labels = {name: self._label(name) for name in modes}
        return [
            readout(f"channels.{name}.value", labels[name], group="Analog", precision=4, unit=mode_unit(mode))
            for name, mode in modes.items()
        ]

    def scan_rate_hz(self) -> float:
        """Scans per second, shared across every channel in the list.

        Derived from the clock the device reports for the list it is holding,
        which is an order of magnitude lower for a list of more than one
        channel than the base clock alone would suggest.
        """
        return self._clock_hz / float(self._srate * self._dec * len(self._modes))

    def state(self) -> dict[str, Any]:
        """Every channel's label and mode, and its latest reading.

        A reading older than ``sample_interval_s`` is refreshed, so the panel
        stays live without a scan per caller. The label is the channel's number
        until someone names it, so a caller putting a reading under a name
        never has to supply the fallback itself.
        """
        with self._lock:
            if not self._connect():
                values: dict[str, float | None] = dict.fromkeys(self._modes)
            else:
                if self._clock() - self._last_sample >= self._sample_interval_s:
                    self._acquire()
                values = dict(self._reading)
            modes = dict(self._modes)
            labels = {name: self._label(name) for name in modes}
        return {
            "channels": {
                name: {
                    "label": labels[name],
                    "mode": mode,
                    "unit": mode_unit(mode),
                    "value": values.get(name),
                }
                for name, mode in modes.items()
            }
        }

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Run a command given as ``{"command": ..., "args": {...}}``."""
        self.command(str(values.get("command", "")), dict(values.get("args") or {}))
        return self.state()

    def _acquire(self) -> dict[str, float | None]:
        """Scan until a few are in hand and keep the last complete one.

        The endpoint is drained first: the stream starts at the first slot of
        the scan list, so anything left in the endpoint would be read as
        channel one and rotate every reading onto the wrong channel.
        """
        transport = self._transport
        if transport is None:
            return dict(self._reading)
        names = list(self._modes)
        self._last_sample = self._clock()
        try:
            self._drain(timeout_ms=5)
            transport.write(_START_ECHO)
            captured = self._capture(transport, 2 * len(names))
        except OSError as exc:
            self._fail(f"acquisition failed: {exc}")
            return dict(self._reading)
        finally:
            self._stop_quietly()
        scans = decode_scans(strip_echo(captured), len(names))
        if not scans:
            log.debug("di2008 returned no complete scan at %.2f Hz", self.scan_rate_hz())
            return dict(self._reading)
        latest = scans[-1]
        self._reading = {name: value_from_code(latest[at], self._modes[name]) for at, name in enumerate(names)}
        return dict(self._reading)

    def _capture(self, transport: UsbTransport, per_scan: int) -> bytes:
        """Read samples until enough scans are in hand or the window closes.

        The window comes from the rate the device reports rather than a fixed
        duration, so a slow scan list is still given long enough to deliver a
        scan and a fast one costs only as long as it takes. An empty read does
        not end the capture: at a slow rate the gap between scans is longer
        than a single read's timeout while more data is still coming.
        """
        buf = bytearray()
        wanted = _CAPTURE_SCANS * per_scan
        deadline = self._clock() + self._capture_window_s()
        while len(buf) < wanted:
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            buf += transport.read(_PACKET_BYTES, min(150, max(1, int(remaining * 1000))))
        return bytes(buf)

    def _channel(self, args: dict[str, Any]) -> str:
        """The channel a command names, rejected when there is no such one."""
        channel = str(args.get("channel", ""))
        if channel not in self._modes:
            raise CommandRejected(f"daq has no channel {channel!r}")
        return channel

    def _capture_window_s(self) -> float:
        """How long to let a capture run, from the rate the device reports."""
        rate = self.scan_rate_hz()
        if rate <= 0:
            return _CAPTURE_LIMIT_S
        # Room for the scans wanted, plus one for the scan already in progress
        # when the capture opened.
        return min(_CAPTURE_LIMIT_S, max(0.05, (_CAPTURE_SCANS + 1) / rate))

    def _command(self, line: str, *, timeout_ms: int = 150) -> bytes:
        """Send one ASCII command and collect whatever it echoes back."""
        transport = self._transport
        if transport is None:
            raise Di2008Error("not connected")
        self._drain(timeout_ms=5)
        transport.write((line + "\r").encode("ascii"))
        out = bytearray()
        while True:
            chunk = transport.read(_PACKET_BYTES, timeout_ms)
            if not chunk:
                break
            out += chunk
        return bytes(out)

    def _configure(self) -> None:
        """Load the scan list and the rate the current modes call for."""
        self._stop_quietly()
        for slot, (name, mode) in enumerate(self._modes.items()):
            self._command(f"slist {slot} {slist_word(int(name) - 1, mode)}")
        self._command(f"srate {self._srate}")
        self._command(f"dec {self._dec}")
        # Without this the device withholds samples until it has a full packet.
        self._command("ps 0")
        # Read back last: the clock the device reports is the one for the list
        # it is now holding, not the one it held before.
        self._clock_hz = self._read_clock_hz()

    def _connect(self) -> bool:
        """Claim the device if it is not already claimed, at most once per interval."""
        if self._transport is not None:
            return True
        now = self._clock()
        if now - self._last_probe < self._probe_interval_s:
            return False
        self._last_probe = now
        try:
            transport = self._open_transport(self._serial_filter)
        except (Di2008Error, OSError) as exc:
            self._unavailable_reason = str(exc)
            return False
        self._transport = transport
        try:
            # A scan left running by an earlier session would otherwise prefix
            # the first reply with samples.
            self._stop_quietly()
            self._drain(timeout_ms=200)
            self._identity = self._read_identity(transport)
            self._configure()
        except (Di2008Error, OSError) as exc:
            self._unavailable_reason = f"DI-2008 did not answer: {exc}"
            self._disconnect()
            return False
        self._unavailable_reason = ""
        return True

    def _disconnect(self) -> None:
        transport, self._transport = self._transport, None
        if transport is None:
            return
        try:
            transport.write(b"stop\r")
        except OSError as exc:
            log.debug("stopping the DI-2008: %s", exc)
        transport.close()

    def _drain(self, *, timeout_ms: int = 30) -> bytes:
        """Read the IN endpoint until it goes quiet."""
        transport = self._transport
        if transport is None:
            return b""
        out = bytearray()
        while True:
            chunk = transport.read(_PACKET_BYTES, timeout_ms)
            if not chunk:
                break
            out += chunk
        return bytes(out)

    def _fail(self, reason: str) -> None:
        """Record why the unit stopped working and let the next probe retry."""
        self._unavailable_reason = reason
        log.debug("di2008: %s", reason)
        self._disconnect()

    def _configure_channels(self, args: dict[str, Any]) -> dict[str, Any]:
        """Set the mode, the label, or both, on any number of channels.

        Every row is checked before any of it is applied, so a bad mode in one
        row leaves the unit as it was rather than half configured. A row may
        carry either field or both, and a channel no row names is left alone,
        which is what lets a suite settle one channel through the same command
        the panel settles all eight with.

        The scan list is reloaded once, after the last mode is in place. Doing
        it per channel would rebuild it eight times to reach the same place.
        """
        rows = args.get("rows")
        if not isinstance(rows, dict) or not rows:
            raise CommandRejected("daq: 'rows' must name at least one channel")
        labels: dict[str, str] = {}
        modes: dict[str, str] = {}
        for key, values in rows.items():
            channel = self._channel({"channel": key})
            if not isinstance(values, dict):
                raise CommandRejected(f"daq: settings for channel {key!r} must be an object")
            if "label" in values:
                # One line of ordinary spacing, short enough to sit under a
                # reading. An empty one puts the channel back to its number.
                labels[channel] = " ".join(str(values["label"]).split())[:_MAX_LABEL]
            if "mode" in values:
                mode = str(values["mode"])
                if mode not in MODES:
                    raise CommandRejected(f"daq: 'mode' must be one of {', '.join(MODES)}")
                modes[channel] = mode
        self._labels.update(labels)
        if modes:
            self._modes.update(modes)
            self._configure()
        return {"channels": {name: self._channel_state(name) for name in self._modes}}

    def _channel_state(self, name: str) -> dict[str, Any]:
        """One channel's label, mode and unit, without its reading."""
        mode = self._modes[name]
        return {"label": self._label(name), "mode": mode, "unit": mode_unit(mode)}

    def _label(self, channel: str) -> str:
        """What a channel's readings are called, its number until it is named."""
        return self._labels[channel] or f"CH {channel}"

    def _read_clock_hz(self) -> float:
        """The clock the device is scanning the loaded list against.

        A unit that will not name one keeps the base clock, which is right for
        a single-channel list and optimistic for a longer one — it costs a
        capture that ends early, not a wrong reading.
        """
        try:
            reported = float(self._info(_INFO_CLOCK))
        except (Di2008Error, OSError, ValueError):
            return float(_BASE_CLOCK_HZ)
        return reported if reported > 0 else float(_BASE_CLOCK_HZ)

    def _read_identity(self, transport: UsbTransport) -> dict[str, str]:
        """Vendor, product, firmware and serial, as the device reports them."""
        identity = {
            "firmware": self._info(_INFO_FIRMWARE),
            "product": self._info(_INFO_PRODUCT),
            "serial": self._info(_INFO_SERIAL),
            "vendor": self._info(_INFO_VENDOR),
        }
        if not identity["product"]:
            raise Di2008Error("no answer to an info query")
        if not identity["serial"]:
            identity["serial"] = transport.serial_number()
        return identity

    def _info(self, number: int) -> str:
        """One ``info`` field, with the query the device echoes back removed."""
        text = self._command(f"info {number}", timeout_ms=200).decode("ascii", errors="replace")
        text = text.strip("\x00\r\n ")
        prefix = f"info {number} "
        return text[len(prefix) :] if text.startswith(prefix) else text

    def _stop_quietly(self) -> None:
        """Halt a scan, ignoring a unit that has already stopped."""
        try:
            self._command("stop", timeout_ms=80)
        except (Di2008Error, OSError) as exc:
            log.debug("di2008 stop ignored: %s", exc)


def _usb_string(util: Any, device: Any, index: Any) -> str:
    """A USB string descriptor, empty when the device will not give it up."""
    try:
        return util.get_string(device, index) or ""
    except Exception:
        return ""
