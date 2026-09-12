"""Reclaiming the camera when the driver has disowned it mid-run, and staggering link reads across chips."""

from __future__ import annotations

from typing import Any

import pytest
from suite.camera import CameraError, Snapshot
from suite.profile import CameraSnapshotProfile
from suite.runner import _fault, _read_one_chip, _take_snapshot


class _FakeCamera:
    """A camera that answers only while ``owned`` is true, like the real driver."""

    def __init__(self, *, owned: bool = True) -> None:
        self.owned = owned
        self.own_calls = 0
        self.shots = 0

    def snapshot(self, *, max_width: int) -> Snapshot:
        self.shots += 1
        if not self.owned:
            raise CameraError("snapshot: camera is not owned: own it before driving it")
        return Snapshot(
            height=48, image=b"\x00", mean_luma=64.0, sequence=self.shots, sharpness=4.0, suffix=".png", width=64
        )

    def own(self) -> None:
        self.own_calls += 1
        self.owned = True


class TestTakeSnapshot:
    def test_a_healthy_camera_is_shot_once(self) -> None:
        camera = _FakeCamera()
        shot = _take_snapshot(camera, max_width=64)  # type: ignore[arg-type]
        assert shot.width == 64
        assert camera.shots == 1
        assert camera.own_calls == 0

    def test_a_dropped_camera_is_reclaimed_and_shot_again(self) -> None:
        camera = _FakeCamera(owned=False)
        shot = _take_snapshot(camera, max_width=64)  # type: ignore[arg-type]
        assert shot.width == 64
        assert camera.shots == 2
        assert camera.own_calls == 1

    def test_a_failure_that_is_not_about_ownership_is_not_retried(self) -> None:
        class _Broken:
            def snapshot(self, *, max_width: int) -> Snapshot:
                raise CameraError("snapshot: the instrument returned no image")

            def own(self) -> None:
                raise AssertionError("must not try to reclaim a camera that answered but gave nothing")

        with pytest.raises(CameraError, match="returned no image"):
            _take_snapshot(_Broken(), max_width=64)  # type: ignore[arg-type]

    def test_a_camera_that_will_not_come_back_still_raises(self) -> None:
        class _Gone:
            def snapshot(self, *, max_width: int) -> Snapshot:
                raise CameraError("snapshot: camera is not owned: own it before driving it")

            def own(self) -> None:
                raise CameraError("set_owned: camera is unavailable: no /dev/video* node is present")

        with pytest.raises(CameraError, match="unavailable"):
            _take_snapshot(_Gone(), max_width=64)  # type: ignore[arg-type]


class _FakeLinkCamera:
    """A camera whose link_register call records which address it was asked for."""

    def __init__(self, *, readings: dict[str, dict[str, Any]] | None = None) -> None:
        self.calls: list[str] = []
        self._readings = readings or {}

    def link_register(self, address: str) -> dict[str, Any]:
        self.calls.append(address)
        if address in self._readings:
            return self._readings[address]
        return {"address": address, "error": "", "link_error": False, "locked": True}


class TestReadOneChip:
    def test_no_addresses_means_no_read(self) -> None:
        camera = _FakeLinkCamera()
        assert _read_one_chip(camera, [], 0) is None  # type: ignore[arg-type]
        assert camera.calls == []

    def test_each_iteration_moves_to_the_next_address(self) -> None:
        camera = _FakeLinkCamera()
        addresses = ["0x50", "0x84"]
        for iteration in range(4):
            _read_one_chip(camera, addresses, iteration)  # type: ignore[arg-type]
        assert camera.calls == ["0x50", "0x84", "0x50", "0x84"]

    def test_a_single_chip_is_read_every_time(self) -> None:
        camera = _FakeLinkCamera()
        for iteration in range(3):
            _read_one_chip(camera, ["0x84"], iteration)  # type: ignore[arg-type]
        assert camera.calls == ["0x84", "0x84", "0x84"]

    def test_a_read_that_fails_is_reported_rather_than_raised(self) -> None:
        class _Broken:
            def link_register(self, address: str) -> dict[str, Any]:
                raise CameraError("link_register: timed out")

        reading = _read_one_chip(_Broken(), ["0x84"], 0)  # type: ignore[arg-type]
        assert reading == {"address": "0x84", "error": "link_register: timed out", "link_error": False, "locked": False}


class TestFault:
    """What makes one sample bad, including what the staggered link read found."""

    def _shot(self, *, mean_luma: float = 64.0, sharpness: float = 4.0) -> Snapshot:
        return Snapshot(
            height=48, image=b"\x00", mean_luma=mean_luma, sequence=1, sharpness=sharpness, suffix=".png", width=64
        )

    def test_no_link_reading_is_not_a_fault(self) -> None:

        assert _fault(self._shot(), 0, None, CameraSnapshotProfile()) == ""

    def test_a_locked_chip_is_not_a_fault(self) -> None:

        link = {"address": "0x84", "error": "", "link_error": False, "locked": True}
        assert _fault(self._shot(), 0, link, CameraSnapshotProfile()) == ""

    def test_an_unlocked_chip_is_a_fault(self) -> None:

        link = {"address": "0x84", "error": "", "link_error": False, "locked": False}
        reason = _fault(self._shot(), 0, link, CameraSnapshotProfile())
        assert "0x84" in reason
        assert "down" in reason

    def test_a_link_read_error_is_a_fault(self) -> None:

        link = {"address": "0x84", "error": "link_register: timed out", "link_error": False, "locked": False}
        reason = _fault(self._shot(), 0, link, CameraSnapshotProfile())
        assert "0x84" in reason
        assert "timed out" in reason

    def test_a_dark_or_saturated_frame_is_never_a_fault(self) -> None:
        """Brightness depends on the scene, not on whether the camera is alive."""
        link = {"address": "0x84", "error": "", "link_error": False, "locked": True}
        assert _fault(self._shot(mean_luma=0.0), 0, link, CameraSnapshotProfile()) == ""
        assert _fault(self._shot(mean_luma=255.0), 0, link, CameraSnapshotProfile()) == ""

    def test_a_defocused_or_blank_frame_is_never_a_fault(self) -> None:
        """Sharpness depends on the scene too, not on the camera's health."""
        link = {"address": "0x84", "error": "", "link_error": False, "locked": True}
        assert _fault(self._shot(sharpness=0.0), 0, link, CameraSnapshotProfile()) == ""
