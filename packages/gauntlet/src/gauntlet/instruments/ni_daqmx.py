"""National Instruments analog input, through the NI-DAQmx driver.

The bench unit is a cDAQ-9171 chassis holding an NI-9238, but nothing here is
written for either: the channels on offer, the voltage ranges they take and
the rates they run at are all read back from whichever module is fitted.

Detail that does not read off a datasheet:

- **The chassis is not the device.** NI-DAQmx lists a CompactDAQ chassis and
  each module in it separately, and only the module has analog inputs, so the
  driver looks for a device with channels rather than for the thing that is
  plugged into USB.
- **A delta-sigma module cannot be read on demand.** Its converter only
  produces samples against a clock, so there is no single-point read to make:
  a sample is a short finite acquisition and the reading is its mean. That is
  also why the rate is the module's own minimum rather than something chosen
  here — the NI-9238 will not run below 1.613 kS/s at all.
- ``nidaqmx`` imports on a host that has never seen NI-DAQmx and fails at
  first use, which is what lets a bench without the driver installed report an
  unavailable instrument rather than fail to start. The driver is a kernel
  module and a shared library installed outside Python, and it is not carried
  by the wheel.
- **A container cannot reach the driver, but it can reach a server.** NI
  packages the driver for Ubuntu and RHEL, and its kernel half belongs to the
  host regardless, so Gauntlet running in a container has no local NI-DAQmx to
  call. NI's own answer is the gRPC device server: it runs on the host beside
  the driver and speaks the same API over TCP. A target naming a server is
  driven through that, and nothing else in this file changes — the same
  ``System`` and the same ``Task``, built against a channel instead of the
  local driver.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import nidaqmx
import nidaqmx.constants
import nidaqmx.errors
import nidaqmx.system

from gauntlet.capabilities.declare import command_field, command_row, readout
from gauntlet.capabilities.registry import CommandRejected

log = logging.getLogger("gauntlet.instruments.ni_daqmx")

# Samples taken per reading. Enough to average the converter's noise down
# without making a sample cost longer than the interval the panel refreshes on.
_SAMPLE_COUNT = 100

# Longest an acquisition may block, whatever rate it was asked for.
_READ_LIMIT_S = 2.0

# Longest channel label kept, in characters. Enough to name what is wired to a
# channel, short enough to sit under the reading without crowding its neighbours.
_MAX_LABEL = 32

# Digits kept in a reading. The NI-9238 converts its 1 V span to 24 bits, so a
# count is tens of nanovolts and microvolts is the coarsest that is not lossy.
_PRECISION = 6


class NiDaqmxError(RuntimeError):
    """The driver is not installed, or no device answered through it."""


# Every way NI-DAQmx reports a device or a driver that did not answer, plus
# this driver's own. One tuple, because every caller here treats them alike:
# the module is gone, and the next probe finds out whether it came back.
_UNREACHABLE = (NiDaqmxError, OSError, nidaqmx.errors.Error)


class NiModule(Protocol):
    """One NI-DAQmx analog input device, as this driver uses it."""

    def channels(self) -> tuple[str, ...]:
        """Short names of the analog inputs, in the order the module lists them."""

    def close(self) -> None: ...

    def identity(self) -> dict[str, str]:
        """Model, serial and chassis, as NI-DAQmx reports them."""

    def name(self) -> str:
        """The DAQmx device name, which is how a channel is addressed."""

    def rate_limits(self) -> tuple[float, float]:
        """Slowest and fastest sample rates the module runs its inputs at."""

    def read(self, channels: tuple[tuple[str, float], ...], rate_hz: float, samples: int) -> list[list[float]]:
        """One finite acquisition: a list of samples per channel, in order."""

    def voltage_ranges(self) -> tuple[float, ...]:
        """The positive limit of each input range the module offers."""


def as_channels(data: list[Any]) -> list[list[float]]:
    """One acquisition as a list of samples per channel.

    A task of one channel answers with a flat list of samples rather than a
    list holding one, so a four-channel module and a one-channel module do not
    come back in the same shape.
    """
    if data and not isinstance(data[0], list):
        return [[float(sample) for sample in data]]
    return [[float(sample) for sample in channel] for channel in data]


def mode_name(limit: float) -> str:
    """The name of the range reaching ``limit`` volts either side of zero.

    Spelled the way the DI-2008 spells its own ranges, so a suite setting a
    mode writes ``"500mv"`` whichever acquisition unit is behind the
    capability.
    """
    if limit < 1.0:
        return f"{_trimmed(limit * 1000.0)}mv"
    return f"{_trimmed(limit)}v"


@dataclass(frozen=True)
class DaqmxTarget:
    """Where to look for a module: which NI-DAQmx to ask, and what to take.

    An empty ``server`` is the driver installed on this machine. An empty
    ``device`` is whichever analog input device answers first.
    """

    device: str = ""
    server: str = ""


def parse_target(text: str) -> DaqmxTarget:
    """What a ``daqmx:`` setting names, in either of its two forms.

    ``cDAQ1Mod1`` is a device driven through the local NI-DAQmx.
    ``//host:31763`` and ``//host:31763/cDAQ1Mod1`` are driven through the gRPC
    device server at that address, which is how Gauntlet in a container reaches
    a driver installed on its host.
    """
    if not text.startswith("//"):
        return DaqmxTarget(device=text)
    server, _, device = text[2:].partition("/")
    return DaqmxTarget(device=device, server=server)


def open_daqmx(target: DaqmxTarget) -> NiModule:
    """Claim the first NI-DAQmx analog input device, or the one named.

    A CompactDAQ chassis is listed beside the modules in it and has no channels
    of its own, so a device without analog inputs is passed over rather than
    reported as one that cannot be read.
    """
    options = _grpc_options(target.server) if target.server else None
    try:
        system = nidaqmx.system.System.remote(options) if options else nidaqmx.system.System.local()
        devices = list(system.devices)
    except nidaqmx.errors.Error as exc:
        _close_options(options)
        where = f"the gRPC device server at {target.server}" if target.server else "NI-DAQmx"
        raise NiDaqmxError(f"{where} did not answer: {exc}") from exc

    for device in devices:
        try:
            if target.device and device.name != target.device:
                continue
            if not list(device.ai_physical_chans):
                continue
        except nidaqmx.errors.Error as exc:
            log.debug("skipping a device that would not describe itself: %s", exc)
            continue
        return _DaqmxModule(device, options)

    _close_options(options)
    if target.device:
        raise NiDaqmxError(f"NI-DAQmx lists no analog input device named {target.device!r}")
    raise NiDaqmxError("NI-DAQmx lists no analog input device")


def _grpc_options(server: str) -> Any:
    """A session on the gRPC device server at ``server``.

    grpc is imported here rather than at module scope so that a bench driving a
    local module never has to have it, and the channel is insecure because the
    server binds a bench's own network and NI ships it configured that way.
    """
    try:
        import grpc
    except ImportError as exc:
        raise NiDaqmxError(f"grpc is not importable, so no server can be reached: {exc}") from exc
    return nidaqmx.GrpcSessionOptions(grpc.insecure_channel(server), "")


def _close_options(options: Any) -> None:
    """Drop the channel a session was built on, for a session that has one."""
    if options is not None:
        options.grpc_channel.close()


class _DaqmxModule:
    """One device NI-DAQmx lists, reduced to what the driver reads from it."""

    def __init__(self, device: Any, options: Any = None) -> None:
        self._device = device
        self._options = options

    def channels(self) -> tuple[str, ...]:
        return tuple(channel.name.rsplit("/", 1)[-1] for channel in self._device.ai_physical_chans)

    def close(self) -> None:
        """Drop the gRPC channel, for a module reached through one.

        A locally driven module holds nothing between acquisitions, so for it
        this does nothing.
        """
        _close_options(self._options)

    def identity(self) -> dict[str, str]:
        """Model, serial and chassis, as NI-DAQmx reports them.

        A device that is not in a chassis has no chassis property at all and
        answers the question with an error rather than with nothing, so asking
        must not be what decides whether the module can be driven.
        """
        serial = int(self._device.serial_num)
        return {
            "chassis": self._chassis(),
            "model": str(self._device.product_type),
            "serial": f"{serial:08X}" if serial else "",
        }

    def _chassis(self) -> str:
        """The chassis this module sits in, empty for a device that is its own."""
        try:
            return str(self._device.compact_daq_chassis_device.name)
        except (AttributeError, nidaqmx.errors.Error):
            return ""

    def name(self) -> str:
        return str(self._device.name)

    def rate_limits(self) -> tuple[float, float]:
        return float(self._device.ai_min_rate), float(self._device.ai_max_multi_chan_rate)

    def read(self, channels: tuple[tuple[str, float], ...], rate_hz: float, samples: int) -> list[list[float]]:
        """One finite acquisition, built and torn down around the read.

        The task is not kept between readings. Holding one would leave the
        module clocking for as long as the instrument is registered, where the
        panel asks for a reading about once a second and a task costs
        milliseconds to build.
        """
        with nidaqmx.Task(grpc_options=self._options) as task:
            for channel, limit in channels:
                task.ai_channels.add_ai_voltage_chan(
                    f"{self.name()}/{channel}",
                    min_val=-limit,
                    max_val=limit,
                )
            task.timing.cfg_samp_clk_timing(
                rate_hz,
                sample_mode=nidaqmx.constants.AcquisitionType.FINITE,
                samps_per_chan=samples,
            )
            timeout = min(_READ_LIMIT_S, max(0.5, 4.0 * samples / rate_hz))
            return as_channels(task.read(number_of_samples_per_channel=samples, timeout=timeout))

    def voltage_ranges(self) -> tuple[float, ...]:
        """The positive limit of each range, from the pairs DAQmx reports.

        ``ai_voltage_rngs`` is a flat list of alternating minimums and
        maximums, so the maximums are every second entry.
        """
        pairs = list(self._device.ai_voltage_rngs)
        return tuple(sorted({float(limit) for limit in pairs[1::2] if float(limit) > 0.0}))


class NiDaqmxDaq:
    """Capability provider backed by an NI-DAQmx analog input module.

    Registered under the same name as the DI-2008 and the simulated unit, so a
    bench gets the same panel and the same command names whichever acquisition
    unit is attached to it.
    """

    name = "daq"

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        instance: str = "daq0",
        open_module: Callable[[DaqmxTarget], NiModule] = open_daqmx,
        probe_interval_s: float = 3.0,
        sample_interval_s: float = 1.0,
        samples: int = _SAMPLE_COUNT,
        target: DaqmxTarget | None = None,
    ) -> None:
        self._clock = clock
        self._instance = instance
        self._lock = threading.RLock()
        self._open_module = open_module
        self._probe_interval_s = probe_interval_s
        self._sample_interval_s = sample_interval_s
        self._samples = samples
        self._target = target or DaqmxTarget()
        self._module: NiModule | None = None
        # Everything below is replaced by what the module reports on connect.
        # Until then there are no channels, which is what an unavailable
        # instrument shows.
        self._identity: dict[str, str] = {}
        self._labels: dict[str, str] = {}
        self._modes: dict[str, str] = {}
        self._ranges: dict[str, float] = {}
        self._rate_hz = 0.0
        self._reading: dict[str, float | None] = {}
        # Far enough in the past that the first probe and the first sample both
        # happen immediately.
        self._last_probe = clock() - probe_interval_s
        self._last_sample = clock() - sample_interval_s
        self._unavailable_reason = "not yet probed"

    def available(self) -> bool:
        """Is the module answering right now.

        Polled by the UI on every refresh, so a disconnected module is
        re-probed at most once per ``probe_interval_s`` and the answer between
        probes is the cached one.
        """
        with self._lock:
            return self._connect()

    def close(self) -> None:
        """Release the module."""
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
        control that sets it, so every channel is settled in one pass. The
        modes on offer are the module's own ranges, so a module with a single
        fixed range offers exactly one.
        """
        with self._lock:
            rows = [
                command_row(name, f"AI {name.removeprefix('ai')}", {"label": self._labels[name], "mode": mode})
                for name, mode in self._modes.items()
            ]
            modes = tuple(self._ranges)
        return [
            {
                "name": "configure",
                "label": "Apply",
                "row_label": "Channel",
                "rows": rows,
                "fields": [
                    command_field("mode", "Range", "string", choices=modes),
                    command_field("label", "Label", "string"),
                ],
            },
            {"name": "sample", "label": "Sample", "fields": []},
        ]

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""
        chassis = self._identity.get("chassis", "")
        serial = self._identity.get("serial", "")
        where = f"NI-DAQmx {self._identity.get('device', self._target.device) or 'device'}"
        if chassis:
            where += f" in {chassis}"
        if self._target.server:
            where += f" on {self._target.server}"
        return where + (f" serial {serial}" if serial else "")

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "Simultaneous analog input, each channel one of the module's voltage ranges.",
            "driver": "ni-daqmx",
            "kind": "daq",
            "model": self._identity.get("model", "NI-DAQmx"),
            "serial": self._identity.get("serial", ""),
            "unavailable_reason": self._unavailable_reason,
        }

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""
        return self._instance

    def primary_command(self) -> str:
        """Taking one acquisition is what an operator comes to this panel for."""
        return "sample"

    def read(self) -> dict[str, Any]:
        """Current state, for suites driving the capability API."""
        return self.state()

    def readouts(self) -> list[dict[str, Any]]:
        """One reading per analog input, named for whatever is wired to it.

        The ranges are not among them. The ``configure`` command already shows
        each on the control that sets it, and a module with one fixed range
        would spend a row of the display saying the same thing eight times.
        """
        with self._lock:
            labels = {name: self._label(name) for name in self._modes}
        return [
            readout(f"channels.{name}.value", label, group="Analog", precision=_PRECISION, unit="V")
            for name, label in labels.items()
        ]

    def sample_rate_hz(self) -> float:
        """Samples per second per channel, the module's own slowest rate.

        Every channel is converted at once, so this is not divided across the
        list the way a multiplexed unit's rate is.
        """
        return self._rate_hz

    def state(self) -> dict[str, Any]:
        """Every channel's label, range and latest reading.

        A reading older than ``sample_interval_s`` is refreshed, so the panel
        stays live without an acquisition per caller. The label is the
        channel's name until someone names it, so a caller putting a reading
        under a name never has to supply the fallback itself.
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
                    "unit": "V",
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
        """Take one acquisition and keep the mean of each channel.

        A delta-sigma converter has no single-point read, so the reading is an
        average over a short window rather than one conversion.
        """
        module = self._module
        if module is None:
            return dict(self._reading)
        names = list(self._modes)
        self._last_sample = self._clock()
        channels = tuple((name, self._ranges[self._modes[name]]) for name in names)
        try:
            data = module.read(channels, self._rate_hz, self._samples)
        except _UNREACHABLE as exc:
            self._fail(f"acquisition failed: {exc}")
            return dict(self._reading)
        if len(data) != len(names):
            log.debug("ni-daqmx answered with %d channels of %d", len(data), len(names))
            return dict(self._reading)
        self._reading = {
            name: round(sum(samples) / len(samples), _PRECISION) if samples else None
            for name, samples in zip(names, data, strict=True)
        }
        return dict(self._reading)

    def _channel(self, name: str) -> str:
        """The channel a row names, rejected when there is no such one."""
        if name not in self._modes:
            raise CommandRejected(f"daq has no channel {name!r}")
        return name

    def _configure_channels(self, args: dict[str, Any]) -> dict[str, Any]:
        """Set the range, the label, or both, on any number of channels.

        Every row is checked before any of it is applied, so a bad range in one
        row leaves the module as it was rather than half configured. A row may
        carry either field or both, and a channel no row names is left alone,
        which is what lets a suite settle one channel through the same command
        the panel settles them all with.
        """
        rows = args.get("rows")
        if not isinstance(rows, dict) or not rows:
            raise CommandRejected("daq: 'rows' must name at least one channel")
        labels: dict[str, str] = {}
        modes: dict[str, str] = {}
        for key, values in rows.items():
            channel = self._channel(str(key))
            if not isinstance(values, dict):
                raise CommandRejected(f"daq: settings for channel {key!r} must be an object")
            if "label" in values:
                # One line of ordinary spacing, short enough to sit under a
                # reading. An empty one puts the channel back to its name.
                labels[channel] = " ".join(str(values["label"]).split())[:_MAX_LABEL]
            if "mode" in values:
                mode = str(values["mode"])
                if mode not in self._ranges:
                    raise CommandRejected(f"daq: 'mode' must be one of {', '.join(self._ranges)}")
                modes[channel] = mode
        self._labels.update(labels)
        self._modes.update(modes)
        return {"channels": {name: self._channel_state(name) for name in self._modes}}

    def _channel_state(self, name: str) -> dict[str, Any]:
        """One channel's label and range, without its reading."""
        return {"label": self._label(name), "mode": self._modes[name], "unit": "V"}

    def _connect(self) -> bool:
        """Find the module if it is not already found, at most once per interval."""
        if self._module is not None:
            return True
        now = self._clock()
        if now - self._last_probe < self._probe_interval_s:
            return False
        self._last_probe = now
        try:
            module = self._open_module(self._target)
        except _UNREACHABLE as exc:
            self._unavailable_reason = str(exc)
            return False
        try:
            self._adopt(module)
        except _UNREACHABLE as exc:
            self._unavailable_reason = f"the module did not describe itself: {exc}"
            module.close()
            return False
        self._module = module
        self._unavailable_reason = ""
        return True

    def _adopt(self, module: NiModule) -> None:
        """Read the module's channels, ranges and rate, and start from them.

        A module offering no range at all cannot be read, so it is refused here
        rather than left to fail on every acquisition.
        """
        ranges = module.voltage_ranges()
        if not ranges:
            raise NiDaqmxError("the module reports no input range")
        channels = module.channels()
        if not channels:
            raise NiDaqmxError("the module reports no analog input")
        self._identity = {**module.identity(), "device": module.name()}
        self._ranges = {mode_name(limit): limit for limit in ranges}
        # The widest range, which is what reads a signal of unknown size
        # without clipping it.
        widest = mode_name(max(ranges))
        self._modes = dict.fromkeys(channels, widest)
        self._labels = dict.fromkeys(channels, "")
        self._reading = dict.fromkeys(channels)
        # A delta-sigma module will not run below its minimum at all, and the
        # slowest rate it does run at is the one that averages down best.
        self._rate_hz = module.rate_limits()[0]

    def _disconnect(self) -> None:
        module, self._module = self._module, None
        if module is not None:
            module.close()

    def _fail(self, reason: str) -> None:
        """Record why the module stopped working and let the next probe retry."""
        self._unavailable_reason = reason
        log.debug("ni-daqmx: %s", reason)
        self._disconnect()

    def _label(self, channel: str) -> str:
        """What a channel's readings are called, its name until it is named."""
        return self._labels[channel] or f"AI {channel.removeprefix('ai')}"


def _trimmed(value: float) -> str:
    """A number written the shortest way that does not change it."""
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return text or "0"
