"""MQTT client for a piezo controller.

Subscribes to each axis's telemetry topic and publishes move commands. The
latest sample per axis is kept so a caller can wait for a target to be reached
without replaying the whole stream.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any


class ControllerError(RuntimeError):
    """The controller could not be reached or commanded."""


def _mqtt() -> Any:
    try:
        from paho.mqtt import client
    except ImportError as exc:
        raise ControllerError("MQTT support needs paho-mqtt: pip install paho-mqtt") from exc
    return client


@dataclass
class Sample:
    """One telemetry sample from an axis."""

    received_at: float
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def position(self) -> int | None:
        value = self.payload.get("position")
        return int(value) if isinstance(value, (int, float)) else None

    @property
    def target_reached(self) -> bool:
        return bool(self.payload.get("target_reached"))

    @property
    def temperature_c(self) -> float | None:
        value = self.payload.get("temperature")
        return float(value) if isinstance(value, (int, float)) else None

    def faults(self) -> list[str]:
        """Fault flags the controller is currently asserting."""
        return [name for name in ("voltage_error", "overheat", "x_limit") if self.payload.get(name)]


def axis_topic(serial: str, axis: int) -> str:
    return f"piezo/{serial}/axis/{axis}"


class PiezoController:
    """Connected MQTT client tracking one controller's axes."""

    def __init__(self, host: str, port: int, *, keepalive_s: int = 30, connect_timeout_s: float = 10.0) -> None:
        mqtt = _mqtt()
        self._client = mqtt.Client()
        self._samples: dict[str, Sample] = {}
        self._lock = threading.Lock()
        self._client.on_message = self._on_message
        try:
            self._client.connect(host, port, keepalive=keepalive_s)
        except OSError as exc:
            raise ControllerError(f"connecting to mqtt://{host}:{port}: {exc}") from exc
        self._client.loop_start()
        self._connect_timeout_s = connect_timeout_s

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._client.loop_stop()
        with contextlib.suppress(Exception):
            self._client.disconnect()

    def subscribe(self, serial: str, axis: int) -> None:
        """Follow one axis's telemetry."""
        self._client.subscribe(f"{axis_topic(serial, axis)}/state")

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        try:
            payload = json.loads(message.payload.decode("utf-8", errors="replace"))
        except (json.JSONDecodeError, AttributeError):
            return
        if not isinstance(payload, dict):
            return
        # Key on the axis topic prefix so /state and any sibling land together.
        key = message.topic.rsplit("/", 1)[0]
        with self._lock:
            self._samples[key] = Sample(received_at=time.time(), payload=payload)

    def latest(self, serial: str, axis: int) -> Sample | None:
        with self._lock:
            return self._samples.get(axis_topic(serial, axis))

    def move(self, serial: str, axis: int, position: int, speed_hz: int) -> None:
        """Command an axis to a position."""
        payload = json.dumps({"position": int(position), "speed": int(speed_hz)})
        result = self._client.publish(f"{axis_topic(serial, axis)}/update", payload, qos=1)
        if result.rc != 0:
            raise ControllerError(f"publishing move for {serial}/{axis}: rc={result.rc}")

    def wait_for_target(self, serial: str, axis: int, position: int, *, timeout_s: float) -> Sample | None:
        """Wait until the axis reports reaching ``position``.

        Returns the sample that confirmed arrival, or ``None`` on timeout.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            sample = self.latest(serial, axis)
            if sample is not None and sample.target_reached and sample.position == position:
                return sample
            time.sleep(0.05)
        return None
