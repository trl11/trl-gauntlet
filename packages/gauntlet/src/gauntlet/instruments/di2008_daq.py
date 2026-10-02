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
import math
import struct
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, Protocol

from gauntlet.capabilities.declare import command_field, command_row, readout
from gauntlet.capabilities.registry import CommandRejected, StreamSlice

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
_CAPTURE_LIMIT_S = 30.0

# How much scan history a stream keeps, and how long it may deliver nothing
# before the unit is taken to have stopped answering. The longest gap measured
# between packets is 45 ms.
_STREAM_HISTORY_S = 35.0
_STREAM_STALL_S = 1.0

# How long one read of the stream waits. The reader holds the lock across it,
# so this is the longest a command or a panel poll can be kept waiting.
_STREAM_READ_MS = 25

# The unit sends samples in packets of this many words however slowly it scans,
# which is why a slow scan arrives in bursts minutes apart.
_PACKET_WORDS = 8

# The slowest the unit can be told to scan: srate counts down from here.
_MAX_SRATE = 2232

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


def _find_units() -> list[Any]:
    """Every DI-2008 on the bus.

    pyusb is imported here rather than at module scope so that a host without
    a usable libusb reports an unavailable instrument instead of failing to
    start.
    """
    try:
        import usb.backend.libusb1
        import usb.core
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
    return found


def candidate_serials() -> list[str]:
    """USB serial numbers of every DI-2008 on the bus, in order.

    Empty when there is none or pyusb cannot look, so a bench without the
    hardware is not an error. The order is by serial number, which makes the
    unit called ``daq0`` the same one on every scan whatever order the bus
    enumerates them in.
    """
    try:
        found = _find_units()
    except Di2008Error:
        return []
    import usb.util

    return sorted(_usb_string(usb.util, unit, unit.iSerialNumber) for unit in found)


def open_usb(serial_filter: str = "") -> UsbTransport:
    """Claim the first DI-2008 on the bus, or one matching ``serial_filter``."""
    found = _find_units()
    import usb.util

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
        self._min_srate = srate
        # Which channels are in the scan list. A shorter list scans faster, so
        # the rate follows this unless an operator asked for a slower one.
        self._active = dict.fromkeys(self._modes, True)
        self._rate_request: float | None = None
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
        # A stream runs while a caller holds a lease. A reader thread ends when
        # the generation it was started under is no longer the current one.
        self._leases = 0
        self._reader: threading.Thread | None = None
        self._ring: deque[tuple[int, float, float, list[float | None]]] = deque()
        self._seq = 0
        self._stream_gen = 0
        self._stream_buf = bytearray()
        self._echo_pending = True
        self._last_data = 0.0
        self._count = 0
        self._mono0 = 0.0
        self._wall0 = 0.0

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
            if name == "scan_rate":
                return self._set_scan_rate(args)
            raise CommandRejected(f"daq has no command {name!r}")

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers.

        One row per channel rather than a control that picks a channel and a
        control that sets it: eight channels are configured in one pass, and
        the scan list is reloaded once for the lot instead of once per change.
        """
        with self._lock:
            rows = [
                command_row(
                    name, f"CH {name}", {"enabled": self._active[name], "label": self._labels[name], "mode": mode}
                )
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
                    command_field("enabled", "Enabled", "boolean"),
                ],
            },
            {
                "name": "scan_rate",
                "label": "Scan rate",
                "role": "header",
                "selected": "scan.selected",
                "current": "scan.rate_hz",
                "unit": "Hz",
                "fields": [command_field("rate", "Scan rate", "string", choices_from="scan.choices")],
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

    def trace_only(self) -> list[str]:
        """What is recorded for the trace and left out of what an operator is shown.

        Which channels are scanning and at what rate explain the readings rather
        than being readings, so they stay in the trace for whoever reads it later.
        """
        return ["scan.auto", "scan.max_hz", "scan.rate_hz", *(f"channels.{name}.enabled" for name in self._modes)]

    def scan_rate_hz(self) -> float:
        """Scans per second, shared across every channel in the list.

        Derived from the clock the device reports for the list it is holding,
        which is an order of magnitude lower for a list of more than one
        channel than the base clock alone would suggest.
        """
        return self._clock_hz / float(self._srate * self._dec * len(self._scan_names()))

    def max_scan_rate_hz(self) -> float:
        """The fastest the current scan list can run, which ``auto`` selects."""
        return self._clock_hz / float(self._min_srate * self._dec * len(self._scan_names()))

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
            active = dict(self._active)
            scan = self._scan_state()
        return {
            "scan": scan,
            "channels": {
                name: {
                    "enabled": active[name],
                    "label": labels[name],
                    "mode": mode,
                    "unit": mode_unit(mode),
                    "value": values.get(name),
                }
                for name, mode in modes.items()
            },
        }

    def stream_channels(self) -> list[dict[str, Any]]:
        """Every channel, whether or not it is in the scan list, with the range it can read."""
        with self._lock:
            return [
                {
                    "enabled": self._active[name],
                    "key": name,
                    "label": self._label(name),
                    "max": value_from_code(32767, mode),
                    "min": value_from_code(-32768, mode),
                    "unit": mode_unit(mode),
                }
                for name, mode in self._modes.items()
            ]

    def stream_enable(self, enabled: dict[str, bool]) -> None:
        """Put channels in or out of the scan list, refused if none would be left."""
        with self._lock:
            if not self._connect():
                raise CommandRejected(f"daq is unavailable: {self._unavailable_reason}")
            self._configure_channels({"rows": {key: {"enabled": value} for key, value in enabled.items()}})

    def stream_close(self) -> None:
        """Release a lease; the last one stops the scan and the reader."""
        with self._lock:
            self._leases = max(0, self._leases - 1)
            if self._leases:
                return
            self._stream_gen += 1
            reader, self._reader = self._reader, None
            if self._transport is not None:
                self._stop_quietly()
        if reader is not None:
            reader.join(timeout=1.0)

    def stream_open(self) -> bool:
        """Take a lease, starting the scan if none is running."""
        with self._lock:
            if not self._connect():
                return False
            if not self._streaming() and not self._stream_begin():
                return False
            self._leases += 1
            return True

    def stream_since(self, seq: int, limit: int) -> StreamSlice:
        """At most ``limit`` buffered scans numbered ``seq`` or more, oldest first."""
        with self._lock:
            newer: list[tuple[int, float, float, list[float | None]]] = []
            for scan in reversed(self._ring):
                if scan[0] < seq:
                    break
                newer.append(scan)
            scans = newer[::-1][:limit]
            return StreamSlice(
                channels=[
                    {
                        "key": name,
                        "label": self._label(name),
                        "max": value_from_code(32767, self._modes[name]),
                        "min": value_from_code(-32768, self._modes[name]),
                        "unit": mode_unit(self._modes[name]),
                    }
                    for name in self._scan_names()
                ],
                rate_hz=self.scan_rate_hz(),
                next_seq=scans[-1][0] + 1 if scans else seq,
                scans=scans,
            )

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
        names = self._scan_names()
        self._last_sample = self._clock()
        if self._streaming():
            # A restarted stream has no scan yet and the reader is waiting on
            # the lock this holds, so the first one is read here. Answering
            # with the reading from before would be the old scan list's.
            deadline = self._clock() + self._capture_window_s()
            while not self._ring and self._clock() < deadline and self._stream_step():
                pass
            if self._ring:
                self._reading = self._reading_of(names, self._ring[-1][3])
            return dict(self._reading)
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
        values: list[float | None] = [value_from_code(latest[at], self._modes[name]) for at, name in enumerate(names)]
        self._reading = self._reading_of(names, values)
        return dict(self._reading)

    def _packet_period_s(self) -> float:
        """Seconds the unit takes to fill one packet at the current scan list and rate."""
        return _PACKET_WORDS / (len(self._scan_names()) * self.scan_rate_hz())

    def _stream_begin(self) -> bool:
        """Start the scan and the thread reading it, with an empty history."""
        transport = self._transport
        if transport is None:
            return False
        self._stream_gen += 1
        self._ring = deque(maxlen=int(_STREAM_HISTORY_S * self.scan_rate_hz()) + 1)
        self._stream_buf = bytearray()
        self._echo_pending = True
        self._last_data = self._clock()
        self._count = 0
        self._mono0 = self._last_data
        self._wall0 = time.time()
        try:
            self._drain(timeout_ms=5)
            transport.write(_START_ECHO)
        except OSError as exc:
            self._fail(f"acquisition failed: {exc}")
            return False
        self._reader = threading.Thread(
            target=self._stream_loop, args=(self._stream_gen,), daemon=True, name=f"{self._instance}-stream"
        )
        self._reader.start()
        return True

    def _stream_ingest(self, buf: bytearray) -> None:
        """Move every complete scan in ``buf`` into the history.

        A scan is timed by how many the unit has made since the stream began, at
        the rate it runs at, and not by when it was read. A read can be late and
        brings what the unit held meanwhile, so reading times would bunch up
        after a delay and leave a hole before it.
        """
        names = self._scan_names()
        channels = len(names)
        count = len(buf) // (2 * channels)
        if not count:
            return
        scans = decode_scans(bytes(buf[: count * 2 * channels]), channels)
        del buf[: count * 2 * channels]
        period = 1.0 / self.scan_rate_hz()
        for codes in scans:
            self._seq += 1
            self._count += 1
            values: list[float | None] = [
                value_from_code(code, self._modes[name]) for code, name in zip(codes, names, strict=True)
            ]
            offset = self._count * period
            self._ring.append((self._seq, self._mono0 + offset, self._wall0 + offset, values))

    def _stream_loop(self, generation: int) -> None:
        """Read packets until the stream is stopped, replaced, or the unit goes quiet."""
        while True:
            with self._lock:
                if generation != self._stream_gen or not self._stream_step():
                    return
            # Gives a command waiting on the lock its turn.
            time.sleep(0.001)

    def _stream_step(self) -> bool:
        """Read one packet into the history. False once the stream has failed."""
        transport = self._transport
        if transport is None:
            return False
        chunk = transport.read(_PACKET_BYTES, _STREAM_READ_MS)
        now = self._clock()
        if chunk:
            self._last_data = now
            if self._echo_pending:
                chunk, self._echo_pending = strip_echo(chunk), False
            self._stream_buf += chunk
            self._stream_ingest(self._stream_buf)
        elif now - self._last_data > max(_STREAM_STALL_S, 3.0 * self._packet_period_s()):
            self._fail("the stream stopped delivering scans")
            return False
        return True

    def _streaming(self) -> bool:
        """Is a reader running under the current generation."""
        return self._reader is not None and self._reader.is_alive()

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
        return min(_CAPTURE_LIMIT_S, max(0.05, (_CAPTURE_SCANS + 1) / rate, self._packet_period_s()))

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
        names = self._scan_names()
        for slot, name in enumerate(names):
            self._command(f"slist {slot} {slist_word(int(name) - 1, self._modes[name])}")
        if len(names) < _ANALOG_COUNT:
            # A list shorter than the eight slots needs an end marker.
            self._command(f"slist {len(names)} 65535")
        self._command(f"dec {self._dec}")
        # The clock the device reports is the one for the list it is now
        # holding, and the rate is worked out from it, so it is read before
        # srate is sent. It does not depend on srate.
        self._clock_hz = self._read_clock_hz()
        self._srate = self._choose_srate(len(names))
        self._command(f"srate {self._srate}")
        # Without this the device withholds samples until it has a full packet.
        # After srate, which puts the packet size back.
        self._command("ps 0")

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
        active: dict[str, bool] = {}
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
            if "enabled" in values:
                if not isinstance(values["enabled"], bool):
                    raise CommandRejected("daq: 'enabled' must be true or false")
                active[channel] = values["enabled"]
        if not any({**self._active, **active}.values()):
            raise CommandRejected("daq: at least one channel must stay enabled")
        self._labels.update(labels)
        if modes or active:
            self._modes.update(modes)
            self._active.update(active)
            self._reload()
        return {"channels": {name: self._channel_state(name) for name in self._modes}}

    def _channel_state(self, name: str) -> dict[str, Any]:
        """One channel's label, mode and unit, without its reading."""
        mode = self._modes[name]
        return {"enabled": self._active[name], "label": self._label(name), "mode": mode, "unit": mode_unit(mode)}

    def _choose_srate(self, channels: int) -> int:
        """The ``srate`` that gives the requested rate, or the fastest for ``auto``.

        Rounded up, so the unit never scans faster than asked.
        """
        if self._rate_request is None:
            return self._min_srate
        wanted = math.ceil(self._clock_hz / (self._dec * channels * self._rate_request))
        return min(_MAX_SRATE, max(self._min_srate, wanted))

    def _reading_of(self, names: list[str], values: list[float | None]) -> dict[str, float | None]:
        """Readings for every channel, empty for one that is not in the scan list."""
        reading: dict[str, float | None] = dict.fromkeys(self._modes)
        reading.update(zip(names, values, strict=True))
        return reading

    def _reload(self) -> None:
        """Load the scan list again, restarting the stream if one is running."""
        streaming = self._streaming()
        self._stream_gen += 1
        self._configure()
        if streaming:
            self._stream_begin()

    def _scan_names(self) -> list[str]:
        """The channels in the scan list, in scan order."""
        return [name for name in self._modes if self._active[name]]

    def _set_scan_rate(self, args: dict[str, Any]) -> dict[str, Any]:
        """Ask for ``auto`` or a rate in Hz no faster than the list can run."""
        text = str(args.get("rate", "")).strip().lower()
        slowest = self._clock_hz / (_MAX_SRATE * self._dec * len(self._scan_names()))
        fastest = self.max_scan_rate_hz()
        request: float | None = None
        if text != "auto":
            try:
                request = float(text)
            except ValueError:
                request = math.nan
            if not slowest <= request <= fastest:
                raise CommandRejected(f"daq: 'rate' must be auto or between {slowest:.2f} and {fastest:.2f} Hz")
        self._rate_request = request
        self._reload()
        return self._scan_state()

    def _scan_state(self) -> dict[str, Any]:
        """The scan rate now, the most the list allows, whether it is ``auto``, and the rates on offer."""
        selected = "auto" if self._rate_request is None else f"{self._rate_request:g}"
        choices = ["auto", *self._rate_choices()]
        if selected not in choices:
            choices.append(selected)
        return {
            "auto": self._rate_request is None,
            "choices": choices,
            "max_hz": self.max_scan_rate_hz(),
            "rate_hz": self.scan_rate_hz(),
            "selected": selected,
        }

    def _rate_choices(self) -> list[str]:
        """Rates the unit can run the current list at, fastest first, each a step of ``srate``."""
        divisors = (1, 2, 4, 5, 10, 20, 40, 100, 200, 400, 1000)
        fastest = self.max_scan_rate_hz()
        slowest = self._clock_hz / (_MAX_SRATE * self._dec * len(self._scan_names()))
        rates = [fastest / divisor for divisor in divisors]
        return [f"{rate:g}" for rate in rates if rate >= slowest]

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
        transport = self._transport
        try:
            if transport is None:
                raise Di2008Error("not connected")
            # Written before anything is drained: a unit that is scanning
            # never goes quiet, so draining first would not end.
            transport.write(b"stop\r")
            self._drain(timeout_ms=80)
        except (Di2008Error, OSError) as exc:
            log.debug("di2008 stop ignored: %s", exc)


def _usb_string(util: Any, device: Any, index: Any) -> str:
    """A USB string descriptor, empty when the device will not give it up."""
    try:
        return util.get_string(device, index) or ""
    except Exception:
        return ""
