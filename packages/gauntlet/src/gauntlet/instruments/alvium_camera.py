"""Capability provider backed by an Allied Vision USB3 Vision camera.

An Alvium is not a UVC device. The kernel binds no driver to it and it gets no
``/dev/video*`` node, so :class:`~gauntlet.instruments.uvc_camera.UvcCamera`
cannot see it at all. The whole camera — descriptors, registers and pixels —
is reached through its one usbfs node by a GenTL transport layer, which VmbC
loads from ``GENICAM_GENTL64_PATH`` and which is not part of the ``vmbpy``
wheel. A bench without that layer installed finds no cameras rather than
failing, which is what ``unavailable_reason`` then says.

The device is exclusive, so nothing here opens one on its own. Presence is
answered from sysfs, the way ``candidate_ports()`` does for the PSU: an
operator polling the panel must not cost a transport-layer startup, and
starting one touches every camera on the bus. The device opens only once
something *owns* it — the panel's latching key, or
``CapabilityRegistry.claim_for_run`` for exactly a run's duration.

The sensor's temperature is a reading worth watching rather than a detail: past
its limit the firmware shuts the image path down, and every acquisition feature
then reports itself unreadable while the camera stays on the bus answering for
its serial and its firmware. Read the temperature before believing a camera
that enumerates but will not say how wide it is.
"""

from __future__ import annotations

import base64
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import vmbpy

from gauntlet.capabilities.declare import command_field, readout
from gauntlet.capabilities.registry import CommandRejected
from gauntlet.instruments.imaging import ImageError, encode_jpeg, encode_png, measure, scale_rgb

log = logging.getLogger("gauntlet.instruments.alvium_camera")

# Allied Vision. A camera answers on 0001 and the same camera answers on ff01
# while its firmware is being written, where it is on the bus but is not a
# camera, so only the first is a candidate.
VENDOR_ID = "1ab2"
PRODUCT_ID = "0001"

_USB_DEVICES = Path("/sys/bus/usb/devices")

# What the kernel reports for a SuperSpeed link, in Mb/s. A USB3 Vision
# camera in a USB 2.0 port enumerates and configures normally and then fails
# every grab, so the negotiated speed is worth reporting on its own.
_SUPERSPEED_MBPS = 5000

# How much memory usbfs will let a process pin for transfers, in MB. A frame
# larger than this cannot be queued whole and arrives incomplete.
_USBFS_LIMIT_MB = Path("/sys/module/usbcore/parameters/usbfs_memory_mb")

# The camera sends RGB already, debayered on the sensor board, so this is the
# one format the encoder is handed. A camera that cannot produce it is not
# usable here and says so rather than being opened and left unreadable.
#
# vmbpy's own enum rather than the name of the GenICam entry, which the camera
# spells RGB8 and vmbpy spells Rgb8: going through the typed call is what keeps
# the two from having to agree here.
_PIXEL_FORMAT = vmbpy.PixelFormat.Rgb8

# No limit, so a frame is encoded at its own width, which is what the panel's
# widest choice takes a picture at.
_FULL_RES = 0
_FULL_RES_CHOICE = "Full"
_MIN_WIDTH = 16
_WIDTH_CHOICES = (_FULL_RES_CHOICE, "1920", "960", "480")

# The 1800 U-2040c's own range is 70us to 10s. Replaced by what the camera
# reports as soon as one is open, so the panel's dial matches the device
# rather than this file.
_EXPOSURE_MIN_US = 1.0
_EXPOSURE_MAX_US = 10_000_000.0

# What the camera calls metering it does for itself.
_AUTO_ON = "Continuous"
_AUTO_OFF = "Off"

_FRAME_TIMEOUT_MS = 20_000

_NOT_OWNED = "not owned"
_NO_CANDIDATE = "no Allied Vision camera is on the USB bus"

# Every way vmbpy reports a device that did not answer. It has no public base
# class for them, so they are named here and caught as one.
_VMB_ERRORS = (
    vmbpy.VmbCameraError,
    vmbpy.VmbFeatureError,
    vmbpy.VmbFrameError,
    vmbpy.VmbSystemError,
    vmbpy.VmbTimeout,
    vmbpy.VmbTransportLayerError,
)


def candidate_cameras() -> list[tuple[str, Path, int]]:
    """Every Allied Vision camera on the bus: serial, usbfs node, link Mb/s.

    Read from sysfs rather than by starting a transport layer, so asking
    whether a camera is there costs nothing and disturbs nothing.
    """
    found = []
    for device in sorted(_USB_DEVICES.glob("*")):
        if _attribute(device, "idVendor") != VENDOR_ID or _attribute(device, "idProduct") != PRODUCT_ID:
            continue
        bus = _attribute(device, "busnum")
        number = _attribute(device, "devnum")
        if not bus or not number:
            continue
        node = Path(f"/dev/bus/usb/{int(bus):03d}/{int(number):03d}")
        speed = _attribute(device, "speed")
        found.append((_attribute(device, "serial"), node, int(float(speed)) if speed else 0))
    return found


class AlviumCamera:
    """One Allied Vision camera, offered as the ``camera`` capability."""

    name = "camera"

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        instance: str = "camera0",
        presence: Callable[[], list[tuple[str, Path, int]]] = candidate_cameras,
        probe_interval_s: float = 3.0,
        serial_filter: str = "",
        system: Callable[[], Any] = vmbpy.VmbSystem.get_instance,
        temperature_interval_s: float = 5.0,
    ) -> None:
        self._camera: Any | None = None
        self._clock = clock
        self._instance = instance
        self._lock = threading.RLock()
        self._presence = presence
        self._probe_interval_s = probe_interval_s
        self._serial_filter = serial_filter
        self._system = system
        self._vmb: Any | None = None
        # Far enough in the past that the first attempt to own it happens
        # immediately.
        self._last_probe = clock() - probe_interval_s
        self._identity: dict[str, str] = {}
        self._format: dict[str, Any] = {}
        self._exposure_range = (_EXPOSURE_MIN_US, _EXPOSURE_MAX_US)
        self._last_frame: dict[str, Any] = {}
        self._snapshots = 0
        self._unavailable_reason = _NOT_OWNED
        self._temperature: dict[str, Any] = {}
        self._temperature_interval_s = temperature_interval_s
        self._temperature_read_at = 0.0

    def available(self) -> bool:
        """Is a camera on the bus, without opening one.

        Polled by the UI on every refresh and checked before a run claims the
        capability, so an owned camera answers from that and an unowned one
        from sysfs. A node that is present but cannot be opened is reported as
        such, because that is the udev rule missing rather than the camera.
        """
        with self._lock:
            if self._camera is not None:
                return True
            candidates = self._matching()
            if not candidates:
                self._unavailable_reason = (
                    f"{self._serial_filter}: not present" if self._serial_filter else _NO_CANDIDATE
                )
                return False
            unreadable = [str(node) for _, node, _ in candidates if not os.access(node, os.R_OK | os.W_OK)]
            if len(unreadable) == len(candidates):
                self._unavailable_reason = (
                    f"{', '.join(unreadable)}: not writable — install the udev rules with `make install-udev-rules`"
                )
                return False
            # Cleared as well as set, or a camera that has come back is shown
            # as available beside the reason it once was not.
            self._unavailable_reason = _NOT_OWNED
            return True

    def close(self) -> None:
        """Release the camera and the transport layer."""
        with self._lock:
            self._disconnect()

    def owned(self) -> bool:
        """Is the camera open right now."""
        with self._lock:
            return self._camera is not None

    def own(self) -> bool:
        """Open the camera unless it already is, at most once per interval."""
        with self._lock:
            return self._connect()

    def disown(self) -> None:
        """Release the camera, if this owns it."""
        with self._lock:
            self._disconnect()

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command and return what it produced."""
        with self._lock:
            if name == "set_owned":
                return self._set_owned(args)
            # Before the ownership check, because a camera the firmware has
            # shut down cannot be owned and rebooting it is the way back.
            if name == "reset":
                return self._reset()
            if self._camera is None:
                raise CommandRejected("camera is not owned: own it before driving it")
            if name == "set_exposure":
                return self._set_exposure(args)
            if name == "set_auto_exposure":
                return self._set_auto_exposure()
            if name == "snapshot":
                return self._snapshot(args)
            raise CommandRejected(f"camera has no command {name!r}")

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers.

        `set_owned` is the latching key: pressing it opens or releases the
        camera. The others are offered whether or not it is open, so the panel
        draws the same controls throughout and a camera the firmware has shut
        down can still be rebooted.

        The two exposure commands share a group, so the panel draws one dial
        with a key each side of it: pin the value, or hand metering back.
        """
        minimum, maximum = self._exposure_range
        return [
            {
                "name": "set_owned",
                "label": "Own Camera",
                "fields": [command_field("owned", "Owned", "boolean")],
            },
            {
                "name": "reset",
                "label": "Reboot Camera",
                "fields": [],
                # Reached while looking at the picture that stopped arriving,
                # so it sits with the viewer's own controls.
                "role": "viewer",
            },
            {
                "name": "set_exposure",
                "label": "Set Exposure",
                "group": "exposure",
                "fields": [
                    command_field(
                        "exposure_us",
                        "Exposure",
                        maximum=maximum,
                        minimum=minimum,
                        unit="us",
                    ),
                ],
            },
            {
                "name": "set_auto_exposure",
                "label": "Auto",
                "group": "exposure",
                "fields": [],
            },
            {
                "name": "snapshot",
                "label": "Take Snapshot",
                "fields": [
                    command_field("max_width", "Resolution", kind="string", choices=_WIDTH_CHOICES, unit="px"),
                ],
                # The result is a picture, so the panel draws it rather than
                # listing what came back.
                "returns": "image",
            },
        ]

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""
        serial = self._identity.get("serial", "")
        node = self._identity.get("node", "")
        return " ".join(part for part in (serial, node) if part) or "no camera"

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "USB3 Vision capture, one still image per command.",
            "driver": "alvium",
            "firmware": self._identity.get("firmware", ""),
            "kind": "camera",
            "model": self._identity.get("model", ""),
            "node": self._identity.get("node", ""),
            "resolution": self._resolution(),
            "serial": self._identity.get("serial", ""),
            "unavailable_reason": self._unavailable_reason,
        }

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""
        return self._instance

    def primary_command(self) -> str:
        """Owning the camera is what opens it, so it gets the full width."""
        return "set_owned"

    def read(self) -> dict[str, Any]:
        """Current state, for suites driving the capability API."""
        return self.state()

    def readouts(self) -> list[dict[str, Any]]:
        """What the camera is set to, how hot it is, and what it last saw.

        The sensor temperature is a headline rather than a detail because it
        is what decides whether the camera will produce a frame at all, and
        because a suite irradiating one plots it against everything else.
        """
        return [
            readout("streaming", "Owned", role="summary"),
            readout("link_mbps", "USB link", role="summary", unit="Mb/s"),
            readout("temperature.sensor", "Sensor", group="Temperature", precision=1, unit="C"),
            readout("temperature.mainboard", "Mainboard", group="Temperature", precision=1, unit="C"),
            readout("temperature.status", "Thermal status", group="Temperature", role="summary"),
            readout("format.width", "Width", group="Format", role="summary", unit="px"),
            readout("format.height", "Height", group="Format", role="summary", unit="px"),
            readout("format.pixel_format", "Pixel format", group="Format", role="summary"),
            readout("format.exposure_us", "Exposure", group="Format", precision=0, role="summary", unit="us"),
            readout("format.exposure_auto", "Metering", group="Format", role="summary"),
            readout("last_frame.mean_luma", "Brightness", group="Last snapshot", precision=1),
            readout("last_frame.sharpness", "Sharpness", group="Last snapshot", precision=2),
            readout("snapshots", "Snapshots", role="viewer"),
        ]

    def state(self) -> dict[str, Any]:
        """What backs the capability, the format in force, and the last snapshot.

        ``driver`` is here rather than only in ``describe()`` because a suite
        sees the capability and not the instrument: several drivers answer
        ``camera``, and one written for this one has no other way to find out
        that a webcam is what it was granted.

        No frame is taken to answer this: the panel polls it, and a poll that
        captured would run the camera whenever the page was open. The
        temperatures are a device read, so they are re-read at most once per
        interval and served from the last one in between.
        """
        with self._lock:
            return {
                "driver": "alvium",
                "format": dict(self._format),
                "last_frame": dict(self._last_frame),
                "link_mbps": self._link_mbps(),
                "node": self._identity.get("node", ""),
                "serial": self._identity.get("serial", ""),
                "snapshots": self._snapshots,
                "streaming": self._camera is not None,
                "temperature": self._read_temperature(force=False),
            }

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Run a command given as ``{"command": ..., "args": {...}}``.

        A snapshot answers with the image itself, because the image is the
        whole result and re-reading state would not carry it.
        """
        name = str(values.get("command", ""))
        result = self.command(name, dict(values.get("args") or {}))
        if name == "snapshot":
            return result
        return self.state()

    def _set_owned(self, args: dict[str, Any]) -> dict[str, Any]:
        """``set_owned``: the latching key that opens or releases the camera."""
        enabled = args.get("owned")
        if not isinstance(enabled, bool):
            raise CommandRejected("camera: 'owned' must be true or false")
        if enabled:
            if not self._connect():
                raise CommandRejected(f"camera is unavailable: {self._unavailable_reason}")
        else:
            self._disconnect()
        return {"owned": enabled}

    def _set_exposure(self, args: dict[str, Any]) -> dict[str, Any]:
        """``set_exposure``: pin how long the sensor integrates, in microseconds.

        Pinning means taking metering off the camera, so this turns off auto
        exposure *and* auto gain before setting the value. A run measuring a
        sensor that is dimming has to have both: either one left on
        compensates for exactly the change being recorded, and the drift reads
        flat through a part that is visibly degrading.
        """
        minimum, maximum = self._exposure_range
        value = args.get("exposure_us")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CommandRejected("camera: 'exposure_us' must be a number")
        if not minimum <= value <= maximum:
            raise CommandRejected(f"camera: 'exposure_us' must be between {minimum} and {maximum}")
        camera = self._camera
        if camera is None:
            raise CommandRejected("camera is unavailable: not open")
        try:
            for name in ("ExposureAuto", "GainAuto"):
                camera.get_feature_by_name(name).set(_AUTO_OFF)
            exposure = camera.get_feature_by_name("ExposureTime")
            exposure.set(float(value))
            # Read back rather than kept, because the camera quantises what it
            # is given and the operator is shown what it settled on.
            self._format["exposure_us"] = float(exposure.get())
            self._format["exposure_auto"] = _AUTO_OFF
        except _VMB_ERRORS as exc:
            raise CommandRejected(f"camera: {exc}") from exc
        return {"exposure_us": self._format["exposure_us"]}

    def _set_auto_exposure(self) -> dict[str, Any]:
        """``set_auto_exposure``: hand metering back to the camera.

        Where a camera opens, so this is the way back from a pinned exposure
        without releasing the device.
        """
        camera = self._camera
        if camera is None:
            raise CommandRejected("camera is unavailable: not open")
        self._format["exposure_auto"] = _meter(camera)
        try:
            self._format["exposure_us"] = float(camera.get_feature_by_name("ExposureTime").get())
        except _VMB_ERRORS as exc:
            raise CommandRejected(f"camera: {exc}") from exc
        return {"exposure_auto": self._format["exposure_auto"]}

    def _reset(self) -> dict[str, Any]:
        """``reset``: reboot the camera's firmware.

        The recovery from a thermal shutdown, which latches until the camera
        restarts and which an operator cannot otherwise clear without
        unplugging it — not an option for a camera inside a chamber. It leaves
        the bus for about a second and comes back on a new bus address, so the
        camera is released here and `set_owned` finds it again.

        A shut-down camera opens but refuses every acquisition feature, so it
        cannot be owned and this cannot require owning it. One that nothing
        owns is opened for the reboot and released again.
        """
        owned = self._camera
        if owned is not None:
            vmb, camera = self._vmb, owned
        else:
            opened = self._open()
            if opened is None:
                raise CommandRejected(f"camera is unavailable: {self._unavailable_reason}")
            vmb, camera = opened
        try:
            camera.get_feature_by_name("DeviceReset").run()
        except _VMB_ERRORS as exc:
            raise CommandRejected(f"camera: {exc}") from exc
        finally:
            if owned is not None:
                self._disconnect()
            else:
                _leave(camera)
                _leave(vmb)
        return {"reset": True}

    def _connect(self) -> bool:
        """Open a camera if one is not open already, at most once per interval.

        The interval holds off repeated *failures*, so the panel's poll does
        not start a transport layer several times a second while nothing is
        there. A probe that succeeded does not arm it: releasing the latching
        key and pressing it again has to answer at once, and it is also how a
        run reclaims a camera it has just rebooted.
        """
        if self._camera is not None:
            return True
        now = self._clock()
        if now - self._last_probe < self._probe_interval_s:
            return False

        opened = self._open()
        if opened is None:
            self._last_probe = now
            return False
        vmb, camera = opened
        serial = ""
        try:
            serial = str(camera.get_serial())
            self._configure(camera)
        except _VMB_ERRORS as exc:
            # Past its temperature limit the firmware shuts the image path
            # down, and every acquisition feature then reads as an access
            # error that says nothing about why. The temperature does, and is
            # still readable, so it is what the operator is told.
            named = f"{serial}: " if serial else ""
            self._unavailable_reason = f"{named}{exc}{_thermal_note(camera)}"
            self._last_probe = now
            _leave(camera)
            _leave(vmb)
            return False

        self._camera = camera
        self._vmb = vmb
        self._identity = {
            "firmware": str(camera.get_feature_by_name("DeviceFirmwareVersion").get()),
            "model": str(camera.get_model()),
            "node": next((str(node) for found, node, _ in self._matching() if found == serial), ""),
            "serial": serial,
        }
        self._unavailable_reason = ""
        self._temperature_read_at = now - self._temperature_interval_s
        log.info("camera %s: %s %s", serial, self._identity["model"], self._resolution())
        return True

    def _open(self) -> tuple[Any, Any] | None:
        """The transport layer and the first matching camera, both entered.

        Opening is separate from settling the format because the two fail for
        different reasons and `reset` needs only the first half: a camera the
        firmware has shut down opens and then refuses everything else.
        """
        if not self._matching():
            self._unavailable_reason = _NO_CANDIDATE
            return None

        vmb = self._system()
        try:
            vmb.__enter__()
        except _VMB_ERRORS as exc:
            # Almost always the transport layer: VmbC carries no GenTL of its
            # own, so a bench that never installed one starts up with an empty
            # bus and says so here rather than reporting no camera.
            self._unavailable_reason = f"the GenTL transport layer did not load: {exc}"
            return None

        reasons = []
        for camera in self._cameras(vmb):
            serial = str(camera.get_serial())
            if self._serial_filter and self._serial_filter != serial:
                continue
            try:
                camera.__enter__()
            except _VMB_ERRORS as exc:
                reasons.append(f"{serial}: {exc}")
                continue
            return vmb, camera

        _leave(vmb)
        self._unavailable_reason = "; ".join(reasons) or _NO_CANDIDATE
        return None

    def _configure(self, camera: Any) -> None:
        """Settle the format and read back what the camera is now set to.

        The pixel format is set rather than accepted, because the encoder is
        handed packed RGB and a camera left on whatever it booted with may be
        sending Bayer or mono.

        Metering is handed to the camera for the same reason. It boots with
        exposure and gain both fixed — 5 ms and none — which is a black frame
        in any room that is not brightly lit, so opening a camera and finding
        nothing in the picture would be the ordinary case. `reset` restores
        those defaults, which is why this runs on every connect.

        A pinned exposure therefore lasts as long as the connection and no
        longer: `set_exposure` overrides this, and releasing the camera or
        rebooting it hands metering back. A run that pins one has to pin it
        again after it recovers a camera.

        Everything else read here is read once: it does not change while the
        camera is owned, and a panel poll must not pay for it.
        """
        camera.set_pixel_format(_PIXEL_FORMAT)
        metering = _meter(camera)
        exposure = camera.get_feature_by_name("ExposureTime")
        self._exposure_range = (float(exposure.get_range()[0]), float(exposure.get_range()[1]))
        self._format = {
            "exposure_auto": metering,
            "exposure_us": float(exposure.get()),
            "height": int(camera.get_feature_by_name("Height").get()),
            "payload_bytes": int(camera.get_feature_by_name("PayloadSize").get()),
            "pixel_format": camera.get_pixel_format().name,
            "width": int(camera.get_feature_by_name("Width").get()),
        }

    def _cameras(self, vmb: Any) -> Iterator[Any]:
        """Every camera the transport layer found, or none if it found none."""
        try:
            return iter(vmb.get_all_cameras())
        except _VMB_ERRORS as exc:
            log.debug("listing cameras: %s", exc)
            return iter(())

    def _disconnect(self) -> None:
        camera, self._camera = self._camera, None
        vmb, self._vmb = self._vmb, None
        self._format = {}
        self._temperature = {}
        self._unavailable_reason = _NOT_OWNED
        if camera is not None:
            _leave(camera)
        if vmb is not None:
            _leave(vmb)

    def _matching(self) -> list[tuple[str, Path, int]]:
        """The cameras on the bus this provider would take."""
        candidates = self._presence()
        if not self._serial_filter:
            return candidates
        return [(serial, node, speed) for serial, node, speed in candidates if serial == self._serial_filter]

    def _link_mbps(self) -> int:
        """How fast the link to the camera negotiated, or 0 with none on the bus.

        Read on every poll rather than kept from the connection, because a
        camera re-plugged into another port comes back on a new link without
        the provider being rebuilt.
        """
        candidates = self._matching()
        return candidates[0][2] if candidates else 0

    def _read_temperature(self, *, force: bool) -> dict[str, Any]:
        """Both temperature sensors and the thermal status the camera reports.

        Three register reads, so an unforced call answers from the last one:
        the panel polls state on every refresh, and the figure does not move
        fast enough to be worth a read each time.
        """
        camera = self._camera
        if camera is None:
            return {}
        now = self._clock()
        if not force and self._temperature and now - self._temperature_read_at < self._temperature_interval_s:
            return dict(self._temperature)

        try:
            temperatures = _temperatures(camera)
        except _VMB_ERRORS as exc:
            # A camera that has stopped answering is the measurement, not a
            # crash, so it is reported in the same shape as a healthy one.
            temperatures = {"status": str(exc)}
        self._temperature = temperatures
        self._temperature_read_at = now
        return dict(self._temperature)

    def _resolution(self) -> str:
        """The format in force, as the operator reads it off the panel."""
        if not self._format:
            return ""
        return f"{self._format.get('width', 0)}x{self._format.get('height', 0)} {self._format.get('pixel_format', '')}"

    def _snapshot(self, args: dict[str, Any]) -> dict[str, Any]:
        """One still image, encoded and measured."""
        camera = self._camera
        if camera is None:
            raise CommandRejected("camera is unavailable: not open")
        max_width = _width_arg(args, int(self._format.get("width", 0)))
        # A view being refreshed wants the bytes over the pixels; one kept as
        # an artifact is worth writing losslessly.
        lossy = bool(args.get("live"))

        try:
            frame = camera.get_frame(timeout_ms=_FRAME_TIMEOUT_MS)
            status = str(frame.get_status()).rsplit(".", 1)[-1]
            if status != "Complete":
                note = _grab_note(self._link_mbps(), int(self._format.get("payload_bytes", 0)))
                raise CommandRejected(f"camera: frame arrived {status.lower()}{note}")
            width, height = int(frame.get_width()), int(frame.get_height())
            buffer = bytes(frame.get_buffer())
        except _VMB_ERRORS as exc:
            # A camera that has been unplugged, or one the firmware has shut
            # down over temperature, answers every grab the same way. Dropping
            # it here is what lets `set_owned` find it again once it is back.
            self._disconnect()
            raise CommandRejected(f"camera: {exc}") from exc

        # Separate from the grab: what the encoder makes of a frame is not the
        # camera failing, so a short buffer is a rejected command rather than
        # a fault that drops the connection.
        try:
            pixels, out_width, out_height = scale_rgb(buffer, width, height, max_width=max_width)
            measured: dict[str, Any] = dict(measure(pixels, out_width, out_height))
            payload = encode_jpeg(pixels, out_width, out_height) if lossy else encode_png(pixels, out_width, out_height)
        except ImageError as exc:
            raise CommandRejected(f"camera: {exc}") from exc

        self._snapshots += 1
        self._last_frame = {
            "bytes": len(payload),
            "height": out_height,
            "width": out_width,
            **measured,
        }
        return {
            "image_base64": base64.b64encode(payload).decode(),
            "suffix": ".jpg" if lossy else ".png",
            "source": {"width": width, "height": height, "pixel_format": self._format.get("pixel_format", "")},
            **self._last_frame,
        }


def _meter(camera: Any) -> str:
    """Let the camera choose its own exposure and gain, and say what it is on.

    A camera that cannot is left as it is and answers with an empty string:
    the frame it produces is still worth having, and a bench lit well enough
    for the boot default does not need this.
    """
    for name in ("ExposureAuto", "GainAuto"):
        try:
            camera.get_feature_by_name(name).set(_AUTO_ON)
        except _VMB_ERRORS as exc:
            log.debug("%s: %s", name, exc)
    try:
        return str(camera.get_feature_by_name("ExposureAuto").get())
    except _VMB_ERRORS as exc:
        log.debug("ExposureAuto: %s", exc)
        return ""


def _temperatures(camera: Any) -> dict[str, Any]:
    """Every temperature sensor the camera carries, and its thermal status."""
    selector = camera.get_feature_by_name("DeviceTemperatureSelector")
    reading = camera.get_feature_by_name("DeviceTemperature")
    temperatures: dict[str, Any] = {}
    for entry in selector.get_available_entries():
        selector.set(entry)
        temperatures[str(entry).lower()] = round(float(reading.get()), 2)
    temperatures["status"] = str(camera.get_feature_by_name("DeviceTemperatureStatus").get())
    return temperatures


def _thermal_note(camera: Any) -> str:
    """What the camera is reading, appended to why it would not open.

    Empty when the temperatures cannot be read either, because then there is
    nothing to add and the original error is the whole story.
    """
    try:
        temperatures = _temperatures(camera)
    except _VMB_ERRORS:
        return ""
    status = temperatures.pop("status", "")
    readings = ", ".join(f"{name} {value}C" for name, value in temperatures.items())
    return f" ({readings}: {status})" if readings else ""


def _grab_note(link_mbps: int, payload_bytes: int) -> str:
    """Why a frame arrived incomplete, where the host is visibly the cause.

    A camera on a USB 2.0 link, and one whose frame is larger than the usbfs
    buffer limit, both open and configure normally and then fail every grab.
    The frame status alone names neither, and both are fixed on the host
    rather than on the camera.
    """
    notes = []
    if link_mbps and link_mbps < _SUPERSPEED_MBPS:
        notes.append(
            f"the camera negotiated a {link_mbps} Mb/s link and USB3 Vision needs "
            f"{_SUPERSPEED_MBPS} Mb/s: check the port and the cable"
        )
    limit = _usbfs_limit_bytes()
    if limit and payload_bytes > limit:
        notes.append(
            f"a {payload_bytes // 1_000_000} MB frame does not fit the "
            f"{limit // 1_000_000} MB usbfs buffer limit: raise usbfs_memory_mb"
        )
    return f" — {'; '.join(notes)}" if notes else ""


def _usbfs_limit_bytes() -> int:
    """How much usbfs will pin for transfers, or 0 where the kernel does not say."""
    try:
        return int(_USBFS_LIMIT_MB.read_text().strip()) * 1_000_000
    except (OSError, ValueError):
        return 0


def _attribute(device: Path, name: str) -> str:
    """One sysfs attribute of a USB device, empty when it has none."""
    try:
        return (device / name).read_text().strip()
    except OSError:
        return ""


def _leave(context: Any) -> None:
    """Close something entered by hand, swallowing what closing it raises.

    Both the camera and the transport layer are held open across calls rather
    than around a block, so there is no `with` to unwind them and a failure to
    close one must not stop the other being closed.
    """
    try:
        context.__exit__(None, None, None)
    except Exception as exc:
        log.debug("releasing %s: %s", type(context).__name__, exc)


def _width_arg(args: dict[str, Any], limit: int) -> int:
    """The width to cap the output at, from a preset name or a number.

    The panel sends the name of a preset and a suite sends a number, so both
    reach the same setting. Absent, or the widest preset, means no cap at all.
    `limit` is the width the camera is set to. Asking for more than that is
    the same as asking for the whole frame rather than an error: the panel
    offers a fixed list of presets and a suite names a number, and neither can
    know how wide the camera on this bench is.
    """
    value = args.get("max_width")
    if value is None or value == "" or value == _FULL_RES_CHOICE:
        return _FULL_RES
    if isinstance(value, str):
        if not value.isdigit():
            raise CommandRejected(f"camera: 'max_width' must be a number or one of {', '.join(_WIDTH_CHOICES)}")
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandRejected("camera: 'max_width' must be a number")
    width = int(value)
    if width < _MIN_WIDTH:
        raise CommandRejected(f"camera: 'max_width' must be at least {_MIN_WIDTH}")
    return _FULL_RES if width >= limit else width
