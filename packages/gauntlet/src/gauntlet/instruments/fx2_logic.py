"""Eight-channel USB logic analyzer built on a Cypress FX2LP (CY7C68013A).

The cheap analyzers sold as a "mini USB logic analyzer" — Xicoolee's among
them — are the same board: an FX2LP with its eight-bit port B wired to the
probes, no on-board acquisition logic and no firmware of its own worth the
name. What makes one a logic analyzer is fx2lafw, sigrok's firmware for the
part, loaded into its RAM over USB. This driver speaks that firmware's
protocol, taken from libsigrok's ``src/hardware/fx2lafw`` and its
``src/ezusb.c``:

- An unprogrammed board answers with the vendor and product ids its EEPROM
  carries — ``0925:3881`` for the Saleae clones, ``04b4:8613`` for a bare
  part. fx2lafw keeps whichever ids it was built for, so what tells a loaded
  device from an unloaded one is its descriptor strings, which read
  ``sigrok`` and ``fx2lafw`` once the firmware is running. That is why the
  ids below are not enough on their own.
- Firmware goes in through the FX2's own bootloader: vendor request ``0xa0``
  writes RAM at the address in ``wValue``. Hold the core in reset by writing
  1 to ``CPUCS`` at ``0xe600``, write the image from address 0 in 4 KiB
  chunks, then release the core with a 0. The board drops off the bus and
  comes back a second or two later running fx2lafw, so a load is one probe
  and the capture is the probe after it.
- Sampling starts with vendor request ``0xb1`` and three bytes: flags, then
  the sample delay high and low. The delay is in ticks of a clock the flags
  pick, 48 or 30 MHz, and only a rate that divides one of them exactly can be
  asked for. 48 MHz is tried first and falls back to 30 MHz where the delay
  would not fit in ``MAX_SAMPLE_DELAY``, which is what lets the slowest rates
  be reached at all.
- Samples then arrive on bulk endpoint 2 IN, one byte per sample and one bit
  per channel, until the host stops reading. There is no stop command: the
  firmware fills the FIFO and stalls there, so a capture drains the endpoint
  before it starts rather than reading the tail of the one before it.

A window is a request rather than a promise. libsigrok keeps thirty-two reads
in flight so the endpoint is never unattended; a synchronous reader has one,
and the gap between two of them is enough for the board to overrun and stop.
Measured on a bench board: a whole window arrives at 1 MHz and below, and at
24 MHz the board delivers one 16 KiB FIFO — 0.68 ms of signal — and then goes
quiet. So a capture reports the window it got rather than the one it asked
for, which for a look at a fast edge is what was wanted anyway.

A read asks for a whole number of endpoint packets and waits about as long as
that many samples take. Both matter: pyusb raises on a timed-out transfer and
throws away whatever had already arrived in it, so one read asking for a
window the board will not deliver in one go loses the samples it did send.

The firmware is sigrok's and is not shipped here. ``logic_firmware`` says
where it is; by default the paths ``sigrok-firmware-fx2lafw`` installs into
are searched, and a board with no firmware to load is registered anyway,
reporting what is missing.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from gauntlet.capabilities.declare import command_field, command_row, readout
from gauntlet.capabilities.registry import CommandRejected
from gauntlet.instruments import waveform

log = logging.getLogger("gauntlet.instruments.fx2_logic")

# Vendor requests. The first is the FX2 bootloader's, and answers before any
# firmware is running; the rest are fx2lafw's.
REQUEST_FIRMWARE = 0xA0
CMD_GET_FW_VERSION = 0xB0
CMD_START = 0xB1
CMD_GET_REVID_VERSION = 0xB2

# The register holding the core in reset, and the largest RAM write the
# bootloader takes.
CPUCS_ADDRESS = 0xE600
FIRMWARE_CHUNK = 4096

# CMD_START flags. Eight-bit sampling is the whole of this board: the wide
# flag is for the sixteen-channel variants, which have no probes here to read.
FLAG_SAMPLE_8BIT = 0 << 5
FLAG_CLK_30MHZ = 0 << 6
FLAG_CLK_48MHZ = 1 << 6

# Six delay states of up to 256 ticks each, which is what puts a floor under
# what the 48 MHz clock can be divided down to.
MAX_SAMPLE_DELAY = 6 * 256

SAMPLE_ENDPOINT = 0x82

# The strings fx2lafw's descriptors carry, whatever ids the board kept.
LOADED_MANUFACTURER = "sigrok"
LOADED_PRODUCT = "fx2lafw"

# Boards this driver knows, and the firmware image each takes. A clone that
# answers with different ids needs a line here and a line in the udev rules,
# and nothing else.
MODELS: dict[tuple[int, int], tuple[str, str]] = {
    (0x04B4, 0x8613): ("Cypress FX2", "fx2lafw-cypress-fx2.fw"),
    (0x0925, 0x3881): ("Saleae Logic", "fx2lafw-saleae-logic.fw"),
    (0x1D50, 0x608C): ("sigrok FX2 LA", "fx2lafw-sigrok-fx2-8ch.fw"),
}

# Where sigrok-firmware-fx2lafw installs, in the order a package manager, a
# hand build and a user install put things.
FIRMWARE_DIRS: tuple[Path, ...] = (
    Path("/usr/share/sigrok-firmware"),
    Path("/usr/local/share/sigrok-firmware"),
    Path.home() / ".local/share/sigrok-firmware",
)

# Sample rates, fastest first so the viewer opens on the most detail. Each
# divides 48 or 30 MHz exactly, which is the only kind the firmware takes.
RATES: dict[str, int] = {
    "24mhz": 24_000_000,
    "16mhz": 16_000_000,
    "12mhz": 12_000_000,
    "8mhz": 8_000_000,
    "6mhz": 6_000_000,
    "4mhz": 4_000_000,
    "3mhz": 3_000_000,
    "2mhz": 2_000_000,
    "1mhz": 1_000_000,
    "500khz": 500_000,
    "250khz": 250_000,
    "200khz": 200_000,
    "100khz": 100_000,
    "50khz": 50_000,
    "25khz": 25_000,
    "20khz": 20_000,
}

# How long a capture covers, shortest first so the viewer opens on the
# cheapest one.
WINDOWS: dict[str, float] = {"1ms": 0.001, "10ms": 0.01, "100ms": 0.1}

# Longest a capture may spend reading, whatever was asked for.
_CAPTURE_LIMIT_S = 3.0

# The most the endpoint is read in one go, which is the board's own FIFO and a
# whole number of the endpoint's 512-byte packets. Reading more than the FIFO
# holds gains nothing and costs the whole transfer when it times out.
_PACKET_BYTES = 512
_READ_CHUNK = 16384

# What a read waits: as long as the samples it asked for take to arrive, half
# again for margin, and never less than this.
_MIN_READ_TIMEOUT_MS = 100
_READ_MARGIN_MS = 50

# What draining the endpoint before a capture may cost. Reading is what lets
# the firmware keep sampling, so a board left streaming hands over bytes for
# as long as it is asked for them and an unbounded drain would never end.
_DRAIN_LIMIT_BYTES = 4 * 1024 * 1024
_DRAIN_LIMIT_S = 0.5
_DRAIN_TIMEOUT_MS = 10

# Longest channel label kept, in characters. As for the acquisition unit:
# enough to name what a probe is clipped to, short enough to sit under a
# reading.
_MAX_LABEL = 32

_USB_TIMEOUT_MS = 100


class Fx2LogicError(RuntimeError):
    """The analyzer could not be reached, or answered with something unusable."""


class UsbTransport(Protocol):
    """One analyzer on the bus, as this driver uses it."""

    def close(self) -> None: ...

    def control_in(self, request: int, size: int) -> bytes: ...

    def control_out(self, request: int, value: int, payload: bytes) -> None: ...

    def identity(self) -> dict[str, str]: ...

    def loaded(self) -> bool: ...

    def read(self, size: int, timeout_ms: int) -> bytes: ...


def firmware_name(vendor: int, product: int) -> str:
    """The fx2lafw image a board with these ids takes."""
    return MODELS.get((vendor, product), ("", ""))[1]


def firmware_file(setting: str, name: str) -> Path | None:
    """Where the image called ``name`` is, or ``None`` if it is nowhere.

    ``setting`` is a path an operator gave: a file is taken as the image
    itself whatever it is called, and a directory is searched for ``name``
    ahead of the installed locations, so a bench with the firmware unpacked
    beside the release needs no package installed.
    """
    named = Path(setting).expanduser() if setting and setting != "auto" else None
    if named is not None and named.is_file():
        return named
    directories = ([named] if named is not None else []) + list(FIRMWARE_DIRS)
    for directory in directories:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


def sample_delay(rate_hz: int) -> tuple[int, int]:
    """The clock flag and delay that give ``rate_hz``.

    48 MHz is tried first, as libsigrok does, and a delay too long to fit
    falls back to the 30 MHz clock, which is what reaches the slowest rates.
    A rate dividing neither clock exactly is refused rather than rounded: a
    capture is measured against the rate it was asked for.
    """
    if rate_hz <= 0:
        raise ValueError("a sample rate must be positive")
    delay = 0
    flags = 0
    if 48_000_000 % rate_hz == 0:
        flags = FLAG_CLK_48MHZ
        delay = 48_000_000 // rate_hz - 1
        if delay > MAX_SAMPLE_DELAY:
            delay = 0
    if delay == 0 and 30_000_000 % rate_hz == 0:
        flags = FLAG_CLK_30MHZ
        delay = 30_000_000 // rate_hz - 1
    if delay <= 0 or delay > MAX_SAMPLE_DELAY:
        raise ValueError(f"{rate_hz} Hz divides neither the 48 nor the 30 MHz clock")
    return flags, delay


def read_size(remaining: int) -> int:
    """How much to ask for, given how much of the window is still to come.

    Whole endpoint packets, and never more than the board's FIFO: a bulk read
    is delivered a packet at a time, and asking for a part of one risks the
    transfer being refused for a packet that will not fit.
    """
    packets = ((remaining + _PACKET_BYTES - 1) // _PACKET_BYTES) * _PACKET_BYTES
    return min(_READ_CHUNK, packets)


def read_timeout_ms(size: int, rate_hz: int) -> int:
    """Long enough for ``size`` samples to arrive at ``rate_hz``, and then some.

    A read that times out loses what had already arrived in it, so waiting too
    briefly for a slow rate throws away the capture rather than shortening it.
    """
    if rate_hz <= 0:
        return _MIN_READ_TIMEOUT_MS
    return max(_MIN_READ_TIMEOUT_MS, int(1500.0 * size / rate_hz) + _READ_MARGIN_MS)


def start_command(rate_hz: int) -> bytes:
    """The three bytes that start a capture at ``rate_hz``."""
    flags, delay = sample_delay(rate_hz)
    return bytes([flags | FLAG_SAMPLE_8BIT, (delay >> 8) & 0xFF, delay & 0xFF])


def upload_firmware(transport: UsbTransport, image: bytes) -> None:
    """Write an fx2lafw image into the board's RAM and let it run.

    The core is held in reset for the whole write. Releasing it renumerates
    the board, so the transport is finished with once this returns.
    """
    transport.control_out(REQUEST_FIRMWARE, CPUCS_ADDRESS, b"\x01")
    for offset in range(0, len(image), FIRMWARE_CHUNK):
        transport.control_out(REQUEST_FIRMWARE, offset, image[offset : offset + FIRMWARE_CHUNK])
    transport.control_out(REQUEST_FIRMWARE, CPUCS_ADDRESS, b"\x00")


def open_usb(serial_filter: str = "") -> UsbTransport:
    """The first analyzer on the bus, or the one whose serial matches.

    pyusb is imported here rather than at module scope so that a host without
    a usable libusb reports an unavailable instrument instead of failing to
    start.
    """
    try:
        import usb.backend.libusb1
        import usb.core
        import usb.util
    except ImportError as exc:
        raise Fx2LogicError(f"pyusb is not importable: {exc}") from exc

    try:
        backend = usb.backend.libusb1.get_backend()
    except Exception as exc:
        raise Fx2LogicError(f"libusb backend unusable: {exc}") from exc
    if backend is None:
        raise Fx2LogicError("no libusb backend: install libusb-1.0-0")

    for vendor, product in MODELS:
        for device in usb.core.find(find_all=True, idVendor=vendor, idProduct=product) or []:
            serial = _usb_string(usb.util, device, device.iSerialNumber)
            if serial_filter and serial_filter not in serial:
                continue
            return _LibusbTransport(
                device,
                model=MODELS[(vendor, product)][0],
                product=_usb_string(usb.util, device, device.iProduct),
                manufacturer=_usb_string(usb.util, device, device.iManufacturer),
                serial=serial,
            )
    if serial_filter:
        raise Fx2LogicError(f"no logic analyzer with serial matching {serial_filter!r}")
    raise Fx2LogicError("no logic analyzer on the USB bus")


class _LibusbTransport:
    """A claimed analyzer, reduced to the transfers the driver makes."""

    def __init__(self, device: Any, *, model: str, product: str, manufacturer: str, serial: str) -> None:
        self._device = device
        self._identity = {
            "manufacturer": manufacturer,
            "model": model,
            "product": product,
            "serial": serial,
            "vendor_id": f"{device.idVendor:04x}",
            "product_id": f"{device.idProduct:04x}",
        }

    def close(self) -> None:
        try:
            import usb.util

            usb.util.dispose_resources(self._device)
        except Exception as exc:
            log.debug("releasing the analyzer: %s", exc)

    def control_in(self, request: int, size: int) -> bytes:
        """One vendor read, empty when the board will not answer."""
        try:
            return bytes(self._device.ctrl_transfer(0xC0, request, 0, 0, size, _USB_TIMEOUT_MS))
        except Exception:
            return b""

    def control_out(self, request: int, value: int, payload: bytes) -> None:
        self._device.ctrl_transfer(0x40, request, value, 0, payload, _USB_TIMEOUT_MS)

    def identity(self) -> dict[str, str]:
        return dict(self._identity)

    def loaded(self) -> bool:
        """Is fx2lafw running, which its descriptor strings are what say."""
        return (
            self._identity["manufacturer"].lower() == LOADED_MANUFACTURER
            and self._identity["product"].lower() == LOADED_PRODUCT
        )

    def read(self, size: int, timeout_ms: int) -> bytes:
        """Whatever is waiting on the sample endpoint, empty when it times out."""
        try:
            return bytes(self._device.read(SAMPLE_ENDPOINT, size, timeout_ms))
        except Exception:
            return b""


class Fx2Logic:
    """Capability provider backed by an FX2LP analyzer running fx2lafw.

    Registered under the same name as the simulated analyzer, so a bench with
    hardware attached gets the same panel and the same command names.
    """

    name = "logic"

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        firmware: str = "auto",
        instance: str = "logic0",
        open_transport: Callable[[str], UsbTransport] = open_usb,
        probe_interval_s: float = 3.0,
        serial_filter: str = "",
    ) -> None:
        self._clock = clock
        self._firmware = firmware
        self._instance = instance
        self._lock = threading.RLock()
        self._open_transport = open_transport
        self._probe_interval_s = probe_interval_s
        self._serial_filter = serial_filter
        self._transport: UsbTransport | None = None
        # What each probe is clipped to, once someone says. Empty until then,
        # which is what makes a reading fall back to its channel number.
        self._labels = {str(number): "" for number in range(1, waveform.CHANNEL_COUNT + 1)}
        self._captures = 0
        self._identity: dict[str, str] = {}
        self._last_capture: dict[str, Any] = {"rate_hz": 0, "samples": 0, "window_s": 0.0}
        self._measured: dict[str, dict[str, Any]] = {}
        # A board seen on the bus, whether or not it could be used. Registering
        # turns on this rather than on availability, so one still waiting for
        # its firmware is shown with the reason rather than hidden.
        self._present = False
        self._last_probe = clock() - probe_interval_s
        self._unavailable_reason = "not yet probed"

    def attached(self) -> bool:
        """Is a board on the bus at all, loaded with firmware or not."""
        with self._lock:
            return self._connect() or self._present

    def available(self) -> bool:
        """Is the analyzer answering right now.

        Polled by the UI on every refresh, so a board that is not usable is
        re-probed at most once per ``probe_interval_s`` and the answer between
        probes is the cached one. A board without firmware is loaded by that
        probe, and answers the probe after the one that loaded it.
        """
        with self._lock:
            return self._connect()

    def close(self) -> None:
        """Release the USB device, leaving the board running as it is."""
        with self._lock:
            self._disconnect()

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command and return what it produced."""
        with self._lock:
            if name == "configure":
                return self._configure_channels(args)
            if not self._connect():
                raise CommandRejected(f"logic is unavailable: {self._unavailable_reason}")
            if name == "capture":
                return self._capture(args)
            raise CommandRejected(f"logic has no command {name!r}")

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers.

        One row per channel to name what is clipped to it, as the acquisition
        unit names what is wired to a channel, and one capture command. Both
        the rate and the window are choices rather than free numbers: the
        firmware takes only rates that divide its two clocks, and the viewer
        draws a choice as a preset beside the capture key.
        """
        with self._lock:
            rows = [command_row(name, f"CH {name}", {"label": label}) for name, label in sorted(self._labels.items())]
        return [
            {
                "name": "configure",
                "label": "Apply",
                "row_label": "Channel",
                "rows": rows,
                "fields": [command_field("label", "Label", "string")],
            },
            {
                "name": "capture",
                "label": "Capture",
                "fields": [
                    command_field("rate", "Sample rate", "string", choices=tuple(RATES)),
                    command_field("window", "Window", "string", choices=tuple(WINDOWS)),
                ],
                # The result is a picture of the capture, so the panel draws
                # it rather than listing what came back.
                "returns": "image",
            },
        ]

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""
        vendor = self._identity.get("vendor_id", "")
        product = self._identity.get("product_id", "")
        serial = self._identity.get("serial", "")
        if not vendor:
            return "no analyzer"
        return f"USB {vendor}:{product}" + (f" serial {serial}" if serial else "")

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "Eight-channel logic capture, one window of samples per command.",
            "driver": "fx2lafw",
            "firmware": self._identity.get("firmware", ""),
            "kind": "logic",
            "model": self._identity.get("model", "FX2LP logic analyzer"),
            "serial": self._identity.get("serial", ""),
            "unavailable_reason": self._unavailable_reason,
        }

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""
        return self._instance

    def primary_command(self) -> str:
        """Taking one window of samples is what this panel is for."""
        return "capture"

    def read(self) -> dict[str, Any]:
        """Current state, for suites driving the capability API."""
        return self.state()

    def readouts(self) -> list[dict[str, Any]]:
        """One reading per probe, named for whatever it is clipped to.

        The level is the display's own figure and the frequency sits beneath
        it, which together say what a logic analyzer is asked: whether a line
        is high, and whether it is moving.
        """
        with self._lock:
            labels = {name: self._label(name) for name in self._labels}
        entries = []
        for name in sorted(labels, key=int):
            entries.append(readout(f"channels.{name}.level", labels[name], group="Channels"))
        for name in sorted(labels, key=int):
            entries.append(
                readout(
                    f"channels.{name}.frequency",
                    labels[name],
                    group="Channels",
                    precision=1,
                    role="summary",
                    unit="Hz",
                )
            )
        entries.append(readout("captures", "Captures", role="viewer"))
        return entries

    def state(self) -> dict[str, Any]:
        """What each probe read over the last capture, and what that capture was."""
        with self._lock:
            channels = {}
            for name in sorted(self._labels, key=int):
                measured = self._measured.get(name, {})
                channels[name] = {
                    "duty": measured.get("duty"),
                    "edges": measured.get("edges"),
                    "frequency": measured.get("frequency"),
                    "label": self._label(name),
                    "level": measured.get("level"),
                }
            return {
                "captures": self._captures,
                "channels": channels,
                "connected": self._transport is not None,
                "last_capture": dict(self._last_capture),
            }

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Run a command given as ``{"command": ..., "args": {...}}``.

        A capture answers with itself rather than with the state, because the
        picture of that window and what was measured in it are the whole
        result, and re-reading state would not carry the picture.
        """
        name = str(values.get("command", ""))
        result = self.command(name, dict(values.get("args") or {}))
        return result if name == "capture" else self.state()

    def _capture(self, args: dict[str, Any]) -> dict[str, Any]:
        """One window of samples, measured and drawn.

        The endpoint is drained first. The firmware never stops sampling of
        its own accord, so whatever the last capture left in the FIFO would
        otherwise be read as the beginning of this one.
        """
        rate_name = str(args.get("rate", next(iter(RATES))))
        window_name = str(args.get("window", next(iter(WINDOWS))))
        if rate_name not in RATES:
            raise CommandRejected(f"logic: 'rate' must be one of {', '.join(RATES)}")
        if window_name not in WINDOWS:
            raise CommandRejected(f"logic: 'window' must be one of {', '.join(WINDOWS)}")
        rate_hz = RATES[rate_name]
        window_s = WINDOWS[window_name]
        wanted = int(rate_hz * window_s)

        transport = self._transport
        if transport is None:
            raise CommandRejected(f"logic is unavailable: {self._unavailable_reason}")
        try:
            self._drain(transport)
            transport.control_out(CMD_START, 0, start_command(rate_hz))
            samples = self._read_samples(transport, wanted, rate_hz)
        except Exception as exc:
            self._disconnect(f"the analyzer stopped answering: {exc}")
            raise CommandRejected(f"logic: capture failed: {exc}") from exc
        if not samples:
            raise CommandRejected("logic: the analyzer sent no samples")

        # What was captured rather than what was asked for: a board that
        # overruns sends less, and every measurement below is of the samples
        # that arrived.
        captured_s = round(len(samples) / rate_hz, 6)
        self._captures += 1
        self._last_capture = {"rate_hz": rate_hz, "samples": len(samples), "window_s": captured_s}
        self._measured = {
            name: waveform.measure(waveform.channel_column(samples, int(name) - 1), rate_hz) for name in self._labels
        }
        return {
            "channels": self.state()["channels"],
            "image_base64": _base64(waveform.render(samples)),
            "rate_hz": rate_hz,
            "samples": len(samples),
            "suffix": ".png",
            "window_s": captured_s,
        }

    def _configure_channels(self, args: dict[str, Any]) -> dict[str, Any]:
        """Name any number of channels, leaving the rest alone.

        Every row is checked before any of it is applied, so a row naming a
        channel that does not exist leaves the labels as they were.
        """
        rows = args.get("rows")
        if not isinstance(rows, dict) or not rows:
            raise CommandRejected("logic: 'rows' must name at least one channel")
        labels: dict[str, str] = {}
        for key, values in rows.items():
            channel = str(key)
            if channel not in self._labels:
                raise CommandRejected(f"logic: no channel {key!r}")
            if not isinstance(values, dict):
                raise CommandRejected(f"logic: settings for channel {key!r} must be an object")
            if "label" in values:
                # One line of ordinary spacing, short enough to sit under a
                # reading. An empty one puts the channel back to its number.
                labels[channel] = " ".join(str(values["label"]).split())[:_MAX_LABEL]
        self._labels.update(labels)
        return {"channels": self.state()["channels"]}

    def _connect(self) -> bool:
        """Open the analyzer if it is not open, loading firmware if it needs it."""
        if self._transport is not None:
            return True
        now = self._clock()
        if now - self._last_probe < self._probe_interval_s:
            return False
        self._last_probe = now
        try:
            transport = self._open_transport(self._serial_filter)
        except Fx2LogicError as exc:
            self._present = False
            self._unavailable_reason = str(exc)
            return False
        self._present = True
        self._identity = transport.identity()
        if not transport.loaded():
            self._load_firmware(transport)
            return False
        version = transport.control_in(CMD_GET_FW_VERSION, 2)
        if len(version) == 2:
            self._identity["firmware"] = f"{version[0]}.{version[1]}"
        self._transport = transport
        self._unavailable_reason = ""
        log.info("logic: %s ready on %s", self._identity.get("model", ""), self.connection())
        return True

    def _disconnect(self, reason: str = "not connected") -> None:
        """Let the device go, leaving whatever it is doing alone."""
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self._unavailable_reason = reason

    def _drain(self, transport: UsbTransport) -> None:
        """Throw away whatever the last capture left in the endpoint.

        Bounded by a byte budget and a deadline as well as by the endpoint
        going quiet, because it may not: the firmware samples for as long as
        the host keeps reading, so a board left streaming can answer every
        read. What is left behind costs nothing — the start command that
        follows re-initialises the FIFO.
        """
        deadline = self._clock() + _DRAIN_LIMIT_S
        drained = 0
        while drained < _DRAIN_LIMIT_BYTES and self._clock() < deadline:
            block = transport.read(_READ_CHUNK, _DRAIN_TIMEOUT_MS)
            if not block:
                return
            drained += len(block)

    def _label(self, channel: str) -> str:
        """What a channel's readings are called, its number until it is named."""
        return self._labels[channel] or f"CH {channel}"

    def _load_firmware(self, transport: UsbTransport) -> None:
        """Put fx2lafw into a board that arrived without it.

        The board renumerates afterwards, so this reports it as unavailable
        with the reason rather than waiting: the next probe finds it running.
        """
        wanted = firmware_name(int(self._identity["vendor_id"], 16), int(self._identity["product_id"], 16))
        path = firmware_file(self._firmware, wanted)
        if path is None:
            transport.close()
            self._unavailable_reason = (
                f"no firmware: {wanted} is in none of "
                f"{', '.join(str(directory) for directory in FIRMWARE_DIRS)}. "
                "Install sigrok-firmware-fx2lafw or set logic_firmware"
            )
            return
        try:
            upload_firmware(transport, path.read_bytes())
        except Exception as exc:
            self._unavailable_reason = f"loading {path} failed: {exc}"
        else:
            self._identity["firmware"] = path.name
            self._unavailable_reason = f"loaded {path.name}; waiting for the analyzer to come back on the bus"
            log.info("logic: loaded %s into %s", path.name, self.connection())
        transport.close()

    def _read_samples(self, transport: UsbTransport, wanted: int, rate_hz: int) -> bytes:
        """Read until the window is full, the board stops sending, or time is up.

        Nothing tells the firmware to stop, and nothing makes it carry on: it
        goes quiet once it has overrun, which at the fastest rates is after one
        FIFO. A short capture is what the board gave, not a failure.
        """
        deadline = self._clock() + _CAPTURE_LIMIT_S
        captured = bytearray()
        while len(captured) < wanted and self._clock() < deadline:
            size = read_size(wanted - len(captured))
            block = transport.read(size, read_timeout_ms(size, rate_hz))
            if not block:
                break
            captured += block
        return bytes(captured[:wanted])


def _base64(payload: bytes) -> str:
    """One file's bytes, as the API carries them."""
    import base64

    return base64.b64encode(payload).decode()


def _usb_string(util: Any, device: Any, index: Any) -> str:
    """A USB string descriptor, empty when the device will not give it up."""
    try:
        return util.get_string(device, index) or ""
    except Exception:
        return ""
