"""Capability provider backed by a USB video camera.

Any UVC camera answers this driver, including a GMSL sensor reached through a
GMSL-to-USB adapter: the adapter presents the sensor as an ordinary capture
device, so nothing here knows a GMSL link from a webcam.

The node is held open and left streaming once a snapshot has been taken.
A capture device is exclusive, so holding it is what stops another process
taking the camera part-way through a run, and starting a 4K stream costs far
more than keeping one running between iterations.

Frames are dropped on the way out of the driver's queue until the newest one
is reached, because a queue that has been sitting still holds whatever the
camera produced when it was last looked at, and a snapshot is meant to be of
now.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gauntlet.capabilities.declare import command_field, readout
from gauntlet.capabilities.registry import CommandRejected
from gauntlet.instruments.imaging import ENCODING_AUTO, ImageError, encode_frame, image_suffix
from gauntlet.instruments.v4l2 import SUPPORTED_FORMATS, V4l2Camera, V4l2Error, capture_devices, fourcc

log = logging.getLogger("gauntlet.instruments.uvc_camera")

_DEFAULT_MAX_WIDTH = 960
_MAX_WIDTH_LIMIT = 3840
_WARMUP_LIMIT = 8


class UvcCamera:
    """One video capture device, offered as the ``camera`` capability."""

    name = "camera"

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        device: str = "",
        frame_format: str = ENCODING_AUTO,
        instance: str = "camera0",
        open_camera: Callable[[Path], V4l2Camera] = V4l2Camera,
        probe_interval_s: float = 3.0,
        warmup_frames: int = 2,
    ) -> None:
        self._camera: V4l2Camera | None = None
        self._clock = clock
        self._device = device
        self._frame_format = frame_format
        self._instance = instance
        self._lock = threading.RLock()
        self._open_camera = open_camera
        self._probe_interval_s = probe_interval_s
        self._warmup_frames = warmup_frames
        # Far enough in the past that the first probe happens immediately.
        self._last_probe = clock() - probe_interval_s
        self._identity: dict[str, str] = {}
        self._format: dict[str, Any] = {}
        self._last_frame: dict[str, Any] = {}
        self._snapshots = 0
        self._unavailable_reason = "not yet probed"

    def available(self) -> bool:
        """Is a camera answering right now.

        Polled by the UI on every refresh, so this reports the connection
        already held and re-probes for an absent camera at most once per
        ``probe_interval_s`` rather than opening the device each time.
        """
        with self._lock:
            return self._connect()

    def close(self) -> None:
        """Stop streaming and release the device."""
        with self._lock:
            self._disconnect()

    def command(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Carry out one command and return what it produced."""
        with self._lock:
            if not self._connect():
                raise CommandRejected(f"camera is unavailable: {self._unavailable_reason}")
            if name == "snapshot":
                return self._snapshot(args)
            raise CommandRejected(f"camera has no command {name!r}")

    def commands(self) -> list[dict[str, Any]]:
        """The commands this instrument offers."""
        return [
            {
                "name": "snapshot",
                "label": "Take Snapshot",
                "fields": [
                    command_field(
                        "max_width",
                        "Width",
                        unit="px",
                        minimum=16,
                        maximum=_MAX_WIDTH_LIMIT,
                    ),
                ],
            }
        ]

    def connection(self) -> str:
        """How the instrument is attached, for the panel subtitle."""
        node = self._identity.get("node", "")
        bus = self._identity.get("bus_info", "")
        return " ".join(part for part in (node, bus) if part) or "no camera"

    def describe(self) -> dict[str, str]:
        """Human-readable detail for the UI and the run manifest."""
        return {
            "description": "USB video capture, one still image per command.",
            "driver": "uvc",
            "kind": "camera",
            "model": self._identity.get("card", ""),
            "node": self._identity.get("node", ""),
            "resolution": self._resolution(),
            "unavailable_reason": self._unavailable_reason,
        }

    def instance_id(self) -> str:
        """Identifier the suite addresses through the API."""
        return self._instance

    def primary_command(self) -> str:
        """Taking a picture is what an operator comes to this panel for."""
        return "snapshot"

    def read(self) -> dict[str, Any]:
        """Current state, for suites driving the capability API."""
        return self.state()

    def readouts(self) -> list[dict[str, Any]]:
        """What the camera is set to, and what the last snapshot looked like."""
        return [
            readout("format.width", "Width", group="Format", role="summary", unit="px"),
            readout("format.height", "Height", group="Format", role="summary", unit="px"),
            readout("format.fourcc", "Pixel format", group="Format", role="summary"),
            readout("last_frame.mean_luma", "Brightness", group="Last snapshot", precision=1),
            readout("last_frame.sharpness", "Sharpness", group="Last snapshot", precision=2),
            readout("snapshots", "Snapshots", group="Last snapshot", role="summary"),
        ]

    def state(self) -> dict[str, Any]:
        """The format in force and what was measured from the last snapshot.

        Reported from what is already known rather than by taking a frame: the
        panel polls this, and a poll that captured would run the camera
        whenever the page was open.
        """
        with self._lock:
            return {
                "format": dict(self._format),
                "last_frame": dict(self._last_frame),
                "node": self._identity.get("node", ""),
                "snapshots": self._snapshots,
                "streaming": self._camera is not None,
            }

    def write(self, values: dict[str, Any]) -> dict[str, Any]:
        """Run a command given as ``{"command": ..., "args": {...}}``.

        A snapshot answers with the image itself rather than with the state,
        because the image is the whole result and re-reading state would not
        carry it.
        """
        name = str(values.get("command", ""))
        result = self.command(name, dict(values.get("args") or {}))
        if name == "snapshot":
            return result
        return self.state()

    def _connect(self) -> bool:
        """Open a camera if one is not open already, at most once per interval."""
        if self._camera is not None:
            return True
        now = self._clock()
        if now - self._last_probe < self._probe_interval_s:
            return False
        self._last_probe = now

        candidates = [Path(self._device)] if self._device else capture_devices()
        if not candidates:
            self._unavailable_reason = "no /dev/video* node is present"
            return False

        reasons = []
        for node in candidates:
            camera = self._open_camera(node)
            try:
                camera.open()
                fmt = camera.format()
                if fmt.get("pixelformat") not in SUPPORTED_FORMATS:
                    raise V4l2Error(f"{node}: {fourcc(int(fmt.get('pixelformat', 0)))} frames are not supported")
                camera.start()
            except V4l2Error as exc:
                camera.close()
                reasons.append(str(exc))
                continue
            self._camera = camera
            self._format = fmt
            self._identity = {"node": str(node), **camera.describe()}
            self._unavailable_reason = ""
            log.info("camera %s: %s %s", node, self._identity.get("card", ""), self._resolution())
            return True

        self._unavailable_reason = "; ".join(reasons)
        return False

    def _disconnect(self) -> None:
        camera, self._camera = self._camera, None
        self._format = {}
        if camera is None:
            return
        try:
            camera.close()
        except V4l2Error as exc:
            log.debug("closing the camera: %s", exc)

    def _resolution(self) -> str:
        """The format in force, as the operator reads it off the panel."""
        if not self._format:
            return ""
        return f"{self._format.get('width', 0)}x{self._format.get('height', 0)} {self._format.get('fourcc', '')}"

    def _snapshot(self, args: dict[str, Any]) -> dict[str, Any]:
        """One still image, encoded and measured, with the stream left running."""
        max_width = _int_arg(args, "max_width", _DEFAULT_MAX_WIDTH, 16, _MAX_WIDTH_LIMIT)
        warmup = _int_arg(args, "warmup", self._warmup_frames, 0, _WARMUP_LIMIT)
        camera = self._camera
        if camera is None:
            raise CommandRejected("camera is unavailable: not open")

        try:
            frame = camera.grab()
            # Everything already queued was captured before this command, so
            # it is thrown away and the frame after it is the one reported.
            for _ in range(warmup):
                frame = camera.grab()
            payload, measured = encode_frame(frame, max_width=max_width, encoding=self._frame_format)
        except (ImageError, V4l2Error) as exc:
            # A camera that has been unplugged answers every grab the same way,
            # so the device is dropped and the next command re-probes for it.
            self._disconnect()
            raise CommandRejected(f"camera: {exc}") from exc

        self._snapshots += 1
        self._last_frame = {
            "bytes": len(payload),
            "sequence": frame.sequence,
            **measured,
        }
        return {
            "image_base64": base64.b64encode(payload).decode(),
            "suffix": image_suffix(frame.pixelformat),
            "source": {"width": frame.width, "height": frame.height, "fourcc": fourcc(frame.pixelformat)},
            **self._last_frame,
        }


def _int_arg(args: dict[str, Any], key: str, fallback: int, minimum: int, maximum: int) -> int:
    """One whole-number argument, rejected when it is not usable.

    Absent means the default, so a panel that offers only some of the fields
    still sends a command the provider accepts.
    """
    value = args.get(key)
    if value is None or value == "":
        return fallback
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandRejected(f"camera: {key!r} must be a number")
    number = int(value)
    if not minimum <= number <= maximum:
        raise CommandRejected(f"camera: {key!r} must be between {minimum} and {maximum}")
    return number
