"""The camera, as this suite reaches it.

Gauntlet owns the device. A suite naming ``camera`` in ``requires:`` is granted
a URL and drives it over HTTP, so nothing here opens a node, loads a transport
layer or knows what USB3 Vision is: the same code reaches any camera behind the
same capability.

``urllib`` rather than a client library, because the SDK depends on pydantic
and pyyaml and a suite may not add to that.
"""

from __future__ import annotations

import base64
import binascii
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class CameraError(RuntimeError):
    """The instrument refused a command, or could not be reached."""


@dataclass(frozen=True)
class Snapshot:
    """One still, and what the instrument measured from it."""

    height: int
    image: bytes
    mean_luma: float
    sharpness: float
    suffix: str
    width: int


@dataclass(frozen=True)
class Temperature:
    """What the camera's own sensors read, and what it makes of them.

    ``status`` is the camera's word rather than a comparison made here, and is
    empty when the camera would not answer. A camera whose image path has shut
    down still answers this one, which is what makes it worth asking for after
    a still has failed.
    """

    mainboard_c: float
    sensor_c: float
    status: str


class Camera:
    """One granted ``camera`` capability."""

    def __init__(self, url: str, *, timeout_s: float = 30.0) -> None:
        self._timeout_s = timeout_s
        self._url = url

    def own(self) -> None:
        """Open the device, so a capture has something to come from.

        Ownership is the driver holding the camera open, and it is held in
        memory, so it does not survive the service restarting. A run that
        assumed the operator had latched it in the panel would fail every
        sample until someone did, which is a wasted beam slot. Asking for it
        costs nothing when it is already owned, so it is also how the suite
        reclaims a camera it has just rebooted.
        """
        self._post({"command": "set_owned", "args": {"owned": True}})

    def reset(self) -> None:
        """Reboot the camera's firmware.

        The only recovery from a thermal shutdown that does not involve
        reaching into the chamber. The camera leaves the bus for about a
        second and comes back on a new address, so it has to be owned again
        afterwards; the driver takes this whether or not anything owns it,
        because a shut-down camera cannot be owned at all.
        """
        self._post({"command": "reset", "args": {}})

    def snapshot(self, *, max_width: int) -> Snapshot:
        """Take one still and return it with its measurements.

        The image comes back base64 encoded inside the JSON reply, because the
        capability endpoint speaks JSON and a still scaled for an artifact is
        small enough that a second binary route would not earn itself.
        """
        payload = self._post({"command": "snapshot", "args": {"max_width": max_width}})
        encoded = payload.get("image_base64")
        if not isinstance(encoded, str) or not encoded:
            raise CameraError("snapshot: the instrument returned no image")
        try:
            image = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise CameraError(f"snapshot: the image was not valid base64: {exc}") from exc
        if not image:
            raise CameraError("snapshot: the image was empty")
        return Snapshot(
            height=_whole(payload.get("height")),
            image=image,
            mean_luma=_number(payload.get("mean_luma")),
            sharpness=_number(payload.get("sharpness")),
            suffix=str(payload.get("suffix") or ".png"),
            width=_whole(payload.get("width")),
        )

    def state(self) -> dict[str, Any]:
        """What the instrument reports about itself, without taking a frame."""
        request = urllib.request.Request(self._url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as reply:
                return dict(json.load(reply))
        except urllib.error.HTTPError as exc:
            raise CameraError(f"state: {_detail(exc)}") from exc
        except (OSError, ValueError) as exc:
            raise CameraError(f"state: {exc}") from exc

    def temperature(self) -> Temperature:
        """Both sensors and the camera's own thermal verdict."""
        reading = self.state().get("temperature")
        if not isinstance(reading, dict):
            return Temperature(mainboard_c=0.0, sensor_c=0.0, status="")
        return Temperature(
            mainboard_c=_number(reading.get("mainboard")),
            sensor_c=_number(reading.get("sensor")),
            status=str(reading.get("status") or ""),
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            self._url,
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as reply:
                return dict(json.load(reply))
        except urllib.error.HTTPError as exc:
            raise CameraError(f"{body['command']}: {_detail(exc)}") from exc
        except (OSError, ValueError) as exc:
            raise CameraError(f"{body['command']}: {exc}") from exc


def _detail(error: urllib.error.HTTPError) -> str:
    """What the instrument said, for an error it explained.

    A rejected command answers 422 carrying the provider's own words, which is
    the difference between "camera is unavailable: not present" and "HTTP 422".
    """
    try:
        payload = json.loads(error.read().decode())
    except (OSError, ValueError, UnicodeDecodeError):
        return f"HTTP {error.code}"
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return str(detail) if detail else f"HTTP {error.code}"


def _number(value: Any) -> float:
    """One measurement, or zero for one the instrument did not report."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _whole(value: Any) -> int:
    """One count, or zero for one the instrument did not report."""
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0
