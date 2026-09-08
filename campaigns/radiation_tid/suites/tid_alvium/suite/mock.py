"""A synthesised camera for runs with no hardware attached.

Answers exactly like :class:`suite.camera.Camera`, so the measurements, the
anomaly rules, the recovery and the verdict all run over mock data unchanged.
This is what `gauntlet verify --run` executes, and what lets the campaign hold
a green baseline for a part that has not been under a beam yet.

The camera degrades with accumulated ticks in the order a real one does under
dose: the sensor warms, then the image loses contrast as dark current fills in
the shadows, then brightness drifts with it, then the image path locks up and
hands back the same frame, and finally it shuts down and stops answering at
all. That last one clears on a reboot and comes back for a while, so a long
mock run exercises the recovery rather than only the failure. A short run stays
healthy, so the conformance profile passes.
"""

from __future__ import annotations

from suite.camera import CameraError, Snapshot, Temperature
from suite.pattern import synthesise

# Ticks before each symptom appears. Well beyond a conformance run, so
# `smoke.yaml` never trips one.
SOFT_ONSET = 40
DIM_ONSET = 70
FREEZE_ONSET = 110
DROPOUT_ONSET = 150

# Ticks a reboot buys before the camera shuts down again, the way clearing a
# thermal latch buys time rather than fixing the heat.
_RECOVERY_TICKS = 25

# How long the sweep takes to cross the frame, in ticks. Long enough that
# consecutive stills differ by a visible step.
_SWEEP_TICKS = 6.0

_IDLE_C = 32.0
_WARM_C = 0.05


class MockCamera:
    """A camera that is not there, degrading the way one under dose does."""

    def __init__(self, *, width: int = 320) -> None:
        self._frozen: Snapshot | None = None
        self._shut_down = False
        self._ticks = 0
        self._width = width
        self._working_until = 0
        self.exposure_us = 0.0

    def own(self) -> None:
        """Take the camera, which a shut-down one refuses the way a real one does."""
        if self._shut_down:
            raise CameraError("set_owned: camera is unavailable: the image path has shut down")

    def reset(self) -> None:
        """Reboot, which clears the latch — the whole point of the command."""
        self._frozen = None
        self._shut_down = False
        self._working_until = self._ticks + _RECOVERY_TICKS

    def set_exposure(self, exposure_us: float) -> float:
        """Take a pinned exposure, which a real camera quantises and reads back."""
        if self._shut_down:
            raise CameraError("set_exposure: camera is unavailable: the image path has shut down")
        self.exposure_us = exposure_us
        return exposure_us

    def snapshot(self, *, max_width: int) -> Snapshot:
        """One still, or the failure the accumulated ticks have earned."""
        self._ticks += 1
        if self._ticks >= DROPOUT_ONSET and self._ticks >= self._working_until:
            self._shut_down = True
        if self._shut_down:
            raise CameraError("snapshot: camera: the camera did not answer")

        # The frozen frame is handed back whole, measurements and all: a
        # locked-up image path repeats a picture that was fine, which is what
        # makes the repeat the only thing that gives it away.
        if self._frozen is not None:
            return self._frozen

        width = min(max_width, self._width)
        image, measured = synthesise(self._ticks / _SWEEP_TICKS, width, max(1, width * 3 // 4))
        shot = Snapshot(
            height=int(measured["height"]),
            image=image,
            mean_luma=self._degraded_luma(measured["mean_luma"]),
            sharpness=self._degraded_sharpness(measured["sharpness"]),
            suffix=".png",
            width=int(measured["width"]),
        )
        if self._ticks >= FREEZE_ONSET:
            self._frozen = shot
        return shot

    def temperature(self) -> Temperature:
        """A sensor that warms as the run goes on, and never quite reaches its limit."""
        sensor = _IDLE_C + self._ticks * _WARM_C
        return Temperature(mainboard_c=sensor - 2.0, sensor_c=round(sensor, 2), status="OK")

    def _degraded_luma(self, luma: float) -> float:
        """Brightness lifting as dark current fills in the shadows."""
        if self._ticks < DIM_ONSET:
            return luma
        return round(luma + (self._ticks - DIM_ONSET) * 0.8, 2)

    def _degraded_sharpness(self, sharpness: float) -> float:
        """Edge detail falling away, which is what moves first."""
        if self._ticks < SOFT_ONSET:
            return sharpness
        return round(sharpness * max(0.0, 1.0 - (self._ticks - SOFT_ONSET) * 0.02), 3)
